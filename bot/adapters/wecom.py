"""企业微信接入（官方合规通道，不会被封号）。

原理：企业微信自建应用接收回调 -> 解密消息 -> 交给人格引擎 -> 调接口主动回复。
需要你有一个企业微信（个人也能免费注册企业），并在后台配置：
    - 应用 AgentId / Secret
    - 接收消息的 URL（需公网可达，可用 ngrok / frp 做内网穿透）
    - Token 和 EncodingAESKey
然后把值填进 config.json 的 wecom 段。

依赖：pip install pycryptodome
"""

from __future__ import annotations

import base64
import hashlib
import json
import queue
import socket
import struct
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def pkcs7_pad(data, block_size=32):
    pad = block_size - (len(data) % block_size)
    if pad == 0:
        pad = block_size
    return data + bytes([pad]) * pad


def pkcs7_unpad(data):
    if not data:
        return data
    pad = data[-1]
    if pad < 1 or pad > 32:
        return data
    return data[:-pad]


def get_signature(token, timestamp, nonce, encrypt):
    parts = sorted([token, str(timestamp), str(nonce), encrypt])
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


class WeComCrypto:
    """企业微信回调消息的加解密（AES-256-CBC + PKCS7 + SHA1 签名）。"""

    def __init__(self, token, encoding_aes_key, receive_id):
        try:
            from Crypto.Cipher import AES  # type: ignore
        except ImportError as exc:
            raise SystemExit(
                "企业微信通道需要 pycryptodome：\n    pip install pycryptodome\n（原始错误：%s）" % exc
            )
        self.aes = AES
        self.token = token or ""
        self.receive_id = receive_id or ""
        key = base64.b64decode((encoding_aes_key or "") + "=")
        if len(key) != 32:
            raise SystemExit("EncodingAESKey 长度不对，应该是 43 个字符")
        self.key = key
        self.iv = key[:16]

    def verify(self, signature, timestamp, nonce, encrypt):
        return get_signature(self.token, timestamp, nonce, encrypt) == signature

    def decrypt(self, encrypt):
        cipher = self.aes.new(self.key, self.aes.MODE_CBC, self.iv)
        plain = pkcs7_unpad(cipher.decrypt(base64.b64decode(encrypt)))
        if len(plain) < 20:
            raise ValueError("解密后数据太短")
        length = struct.unpack("!I", plain[16:20])[0]
        message = plain[20 : 20 + length].decode("utf-8")
        receive_id = plain[20 + length :].decode("utf-8", "replace")
        if self.receive_id and receive_id and receive_id != self.receive_id:
            raise ValueError("receive_id 不匹配，可能是配置填错了")
        return message

    def encrypt(self, message):
        raw = message.encode("utf-8")
        payload = b"0123456789abcdef" + struct.pack("!I", len(raw)) + raw + self.receive_id.encode("utf-8")
        cipher = self.aes.new(self.key, self.aes.MODE_CBC, self.iv)
        return base64.b64encode(cipher.encrypt(pkcs7_pad(payload))).decode("utf-8")


class WeComClient:
    """企业微信接口客户端：拿 access_token + 主动发消息。"""

    BASE = "https://qyapi.weixin.qq.com/cgi-bin"

    def __init__(self, corp_id, secret, agent_id):
        self.corp_id = corp_id
        self.secret = secret
        self.agent_id = agent_id
        self._token = ""
        self._token_expire = 0

    def token(self):
        if self._token and time.time() < self._token_expire:
            return self._token
        url = "%s/gettoken?corpid=%s&corpsecret=%s" % (self.BASE, self.corp_id, self.secret)
        with urllib.request.urlopen(url, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        if data.get("errcode"):
            raise RuntimeError("获取 access_token 失败：%s" % data)
        self._token = data["access_token"]
        self._token_expire = time.time() + int(data.get("expires_in", 7200)) - 300
        return self._token

    def send_text(self, to_user, content):
        url = "%s/message/send?access_token=%s" % (self.BASE, self.token())
        payload = {
            "touser": to_user,
            "msgtype": "text",
            "agentid": int(self.agent_id),
            "text": {"content": content},
            "safe": 0,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        if data.get("errcode"):
            raise RuntimeError("发送失败：%s" % data)
        return data


class WeComAdapter:
    name = "企业微信"

    def __init__(self, cfg, engine):
        self.cfg = cfg
        self.engine = engine
        wecom = cfg.get("wecom", {})
        self.crypto = WeComCrypto(wecom.get("token", ""), wecom.get("encoding_aes_key", ""), wecom.get("corp_id", ""))
        self.client = WeComClient(wecom.get("corp_id", ""), wecom.get("secret", ""), wecom.get("agent_id", ""))
        self.queue = queue.Queue()
        self.host = wecom.get("host", "0.0.0.0")
        self.port = int(wecom.get("port", 8000))
        self.dry_run = bool(cfg.get("behavior", {}).get("dry_run", True))

    def _verify_url(self, query):
        signature = query.get("msg_signature", [""])[0]
        timestamp = query.get("timestamp", [""])[0]
        nonce = query.get("nonce", [""])[0]
        echostr = query.get("echostr", [""])[0]
        if not self.crypto.verify(signature, timestamp, nonce, echostr):
            return None
        try:
            return self.crypto.decrypt(echostr)
        except Exception as exc:  # noqa: BLE001
            print("[企业微信] 校验失败：%s" % exc)
            return None

    def _on_message(self, query, body):
        signature = query.get("msg_signature", [""])[0]
        timestamp = query.get("timestamp", [""])[0]
        nonce = query.get("nonce", [""])[0]
        try:
            envelope = ET.fromstring(body)
        except ET.ParseError:
            return
        encrypt = envelope.findtext("Encrypt") or ""
        if not self.crypto.verify(signature, timestamp, nonce, encrypt):
            print("[企业微信] 签名校验不通过，忽略")
            return
        try:
            plain = self.crypto.decrypt(encrypt)
        except Exception as exc:  # noqa: BLE001
            print("[企业微信] 解密失败：%s" % exc)
            return
        root = ET.fromstring(plain)
        msg_type = root.findtext("MsgType") or ""
        sender = root.findtext("FromUserName") or ""
        if msg_type == "text":
            content = root.findtext("Content") or ""
        elif msg_type == "voice":
            content = "[语音消息]"
        elif msg_type == "image":
            content = "[图片]"
        else:
            return
        self.queue.put((sender, content))

    def _worker(self):
        while True:
            sender, content = self.queue.get()
            if content.startswith("[") and content.endswith("]"):
                print("[企业微信] 跳过非文本消息：%s" % content)
                continue
            try:
                plan = self.engine.plan(sender, sender, content)
            except Exception as exc:  # noqa: BLE001
                print("[企业微信] 生成失败：%s" % exc)
                continue
            if not plan.send or not plan.chunks:
                print("[企业微信] 不回 %s：%s" % (sender, plan.reason))
                continue
            if self.dry_run:
                print("[演练模式] 拟回复 %s：%s" % (sender, " ｜ ".join(plan.chunks)))
                continue
            for chunk, delay in zip(plan.chunks, plan.delays):
                time.sleep(delay)
                try:
                    self.client.send_text(sender, chunk)
                    print("[企业微信] -> %s：%s" % (sender, chunk))
                except Exception as exc:  # noqa: BLE001
                    print("[企业微信] 发送失败：%s" % exc)

    def run(self, poll_interval=None):
        adapter = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def do_GET(self):
                parsed = urlparse(self.path)
                result = adapter._verify_url(parse_qs(parsed.query))
                if result is None:
                    self.send_response(403)
                    self.end_headers()
                    self.wfile.write(b"failed")
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(result.encode("utf-8"))

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0) or 0)
                body = self.rfile.read(length).decode("utf-8", "replace")
                parsed = urlparse(self.path)
                try:
                    adapter._on_message(parse_qs(parsed.query), body)
                except Exception as exc:  # noqa: BLE001
                    print("[企业微信] 处理回调出错：%s" % exc)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"success")

        threading.Thread(target=self._worker, daemon=True).start()
        server = ThreadingHTTPServer((self.host, self.port), Handler)
        print("企业微信回调服务已启动：http://%s:%d" % (self.host, self.port))
        print("把这个地址（需公网可达）填到企业微信后台的接收消息 URL 里")
        print("模式：%s" % ("演练（只打印不发送）" if self.dry_run else "真实发送"))
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止")
        finally:
            server.server_close()


def is_port_free(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        return sock.connect_ex((host, port)) != 0
    finally:
        sock.close()
