"""QQ 接入：通过 NapCat（OneBot v11 协议端）把你的 QQ 小号变成聊天机器人。

原理：
    NapCat 用小号登录电脑上的 QQ（QQNT 内核），把收到的消息以 HTTP 上报的形式
    POST 给本程序；本程序生成回复后，调用 NapCat 的 HTTP API（send_private_msg）发出去。
    于是你的主 QQ 只要加这个小号为好友，点开聊天就能跟它说话。

准备工作：
    1. 装 NapCat：去 NapCatQQ 的 Releases 下载 NapCat.Shell.zip 解压，
       双击 launcher.bat（Win10 用 launcher-win10.bat），或直接用 Windows 一键版
    2. 在 NapCat 的 WebUI 里开启：HTTP 服务（默认 3000 端口）
       以及 HTTP 上报，上报地址填 http://127.0.0.1:5700/onebot
    3. config.json 里 onebot.api_base / host / port / path 和上面保持一致

风险：QQ 协议端同样有风控风险。务必用小号、控制频率、别群发、别加陌生人。
"""

from __future__ import annotations

import json
import os
import queue
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .base import Adapter, Incoming
from ..sanitize import clean

# 非文字消息的占位说法，让模型知道对方发了什么，从而给出合适的回应
SEGMENT_LABELS = {
    "face": "[表情]",
    "image": "[图片]",
    "record": "[语音]",
    "video": "[视频]",
    "file": "[文件]",
    "reply": "",
    "at": "",
    "json": "[卡片消息]",
    "forward": "[聊天记录]",
    "dice": "[骰子]",
    "rps": "[猜拳]",
}


class OneBotHTTPServer(ThreadingHTTPServer):
    """接收 NapCat 上报的 HTTP 服务。

    Windows 上必须关掉 SO_REUSEADDR：否则重复启动多个实例时，它们能同时绑定
    同一个端口，消息被哪个进程接走是随机的——表现出来就是"两个窗口都像没反应"。
    关掉之后，第二个实例会直接报"端口被占用"，用户一眼就知道该关掉旧的。
    """

    daemon_threads = True


if os.name == "nt":
    OneBotHTTPServer.allow_reuse_address = False


class OneBotAdapter(Adapter):
    name = "QQ"

    def __init__(self, cfg, engine):
        super().__init__(cfg, engine)
        onebot = cfg.get("onebot", {})
        self.api_base = (onebot.get("api_base") or "http://127.0.0.1:3000").rstrip("/")
        self.access_token = onebot.get("access_token") or ""
        self.host = onebot.get("host") or "127.0.0.1"
        self.port = int(onebot.get("port") or 5700)
        self.path = onebot.get("path") or "/onebot"
        self.queue = queue.Queue()
        self.dry_run = bool(cfg.get("behavior", {}).get("dry_run", True))
        self.groups_enabled = bool(cfg.get("behavior", {}).get("groups_enabled"))
        self.self_id = str(onebot.get("self_id") or "")

    # ---------------- 调用 NapCat HTTP API ----------------
    def _post(self, endpoint, payload, timeout=15):
        url = self.api_base + endpoint
        if self.access_token and "?" not in url:
            url = url + "?access_token=" + self.access_token
        body = clean(json.dumps(payload, ensure_ascii=False)).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        if self.access_token:
            request.add_header("Authorization", "Bearer " + self.access_token)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))

    def send(self, chat_id, text):
        data = self._post(
            "/send_private_msg",
            {"user_id": int(chat_id), "message": text, "auto_escape": False},
        )
        if data.get("status") == "failed" or data.get("retcode") not in (0, None):
            raise RuntimeError("发送失败：%s" % data)
        return data

    def check(self):
        """自检：能不能连上 NapCat、当前登录的是哪个 QQ。"""
        try:
            info = self._post("/get_login_info", {})
        except urllib.error.URLError as exc:
            print("  [失败] 连不上 NapCat（%s）：%s" % (self.api_base, exc))
            print("  请确认 NapCat 已启动，且 WebUI 里开了 HTTP 服务（默认端口 3000）")
            return False
        except Exception as exc:  # noqa: BLE001
            print("  [失败] 调用 NapCat 出错：%s" % exc)
            return False
        data = info.get("data") or {}
        print("  [通过] 连上 NapCat ✓")
        print("  登录的 QQ：%s（昵称：%s）" % (data.get("user_id", "?"), data.get("nickname", "?")))
        print("  上报地址要填：http://%s:%d%s" % (self.host, self.port, self.path))
        return True

    # ---------------- 事件解析 ----------------
    @staticmethod
    def _extract_text(message):
        if isinstance(message, str):
            return message.strip()
        parts = []
        if isinstance(message, list):
            for segment in message:
                if not isinstance(segment, dict):
                    continue
                kind = segment.get("type")
                data = segment.get("data") or {}
                if kind == "text":
                    parts.append(str(data.get("text", "")))
                elif kind in SEGMENT_LABELS:
                    parts.append(SEGMENT_LABELS[kind])
        return "".join(parts).strip()

    def _to_incoming(self, event):
        if not isinstance(event, dict) or event.get("post_type") != "message":
            return None
        user_id = event.get("user_id")
        if not user_id:
            return None
        self_id = str(event.get("self_id") or self.self_id or "")
        if self_id and str(user_id) == self_id:
            return None
        text = self._extract_text(event.get("message"))
        if not text:
            return None
        is_group = event.get("message_type") == "group"
        if is_group and not self.groups_enabled:
            return None
        sender = event.get("sender") or {}
        name = sender.get("card") or sender.get("nickname") or str(user_id)
        return Incoming(
            chat_id=str(user_id),
            sender=clean(str(name)),
            text=clean(text),
            is_group=is_group,
            key=str(event.get("message_id", "")),
        )

    # ---------------- 主循环 ----------------
    def _worker(self):
        while True:
            item = self.queue.get()
            try:
                self.handle(item)
            except Exception as exc:  # noqa: BLE001
                print("[QQ] 处理消息出错：%s" % exc)

    def run(self, poll_interval=None):
        adapter = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def _read_body(self):
                """读取请求体。有的 HTTP 客户端（NapCat 就是）用分块传输，
                这种请求没有 Content-Length，只按 Content-Length 读会读到空内容。"""
                encoding = (self.headers.get("Transfer-Encoding") or "").lower()
                if "chunked" in encoding:
                    chunks = []
                    while True:
                        size_line = self.rfile.readline().strip()
                        if not size_line:
                            continue
                        try:
                            size = int(size_line.split(b";")[0], 16)
                        except ValueError:
                            break
                        if size <= 0:
                            self.rfile.readline()
                            break
                        chunks.append(self.rfile.read(size))
                        self.rfile.readline()
                    return b"".join(chunks)
                length = int(self.headers.get("Content-Length", 0) or 0)
                return self.rfile.read(length)

            def do_POST(self):
                raw = self._read_body().decode("utf-8", "replace")
                # 先回 200，避免 NapCat 以为上报失败而重发
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")
                try:
                    event = json.loads(raw)
                except ValueError:
                    detail = "空内容" if not raw.strip() else raw[:120]
                    print(
                        "[QQ] 收到无法解析的上报：%s（Content-Length=%s，Transfer-Encoding=%s）"
                        % (detail, self.headers.get("Content-Length"), self.headers.get("Transfer-Encoding"))
                    )
                    return
                incoming = adapter._to_incoming(event)
                if incoming:
                    print("[QQ] 收到消息：%s 说「%s」" % (incoming.sender, incoming.text))
                    adapter.queue.put(incoming)
                elif event.get("post_type") == "message":
                    # 消息类事件被过滤掉了，打印出来方便排查（比如群消息、自己的消息）
                    print(
                        "[QQ] 收到消息事件但被忽略：type=%s user_id=%s self_id=%s"
                        % (event.get("message_type"), event.get("user_id"), event.get("self_id"))
                    )

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"persona bot is running")

        threading.Thread(target=self._worker, daemon=True).start()
        try:
            server = OneBotHTTPServer((self.host, self.port), Handler)
        except OSError as exc:
            print("")
            print("启动失败：%s:%d 这个端口已经被占用了。" % (self.host, self.port))
            print("多半是**已经开着一个 QQ 窗口**在跑同一个机器人。")
            print("请把之前那些黑色窗口都关掉（Alt+F4 或点右上角 ×），再重新双击「QQ」。")
            print("（技术信息：%s）" % exc)
            return
        print("=" * 60)
        print("QQ 机器人已启动（人格：%s）" % self.engine.name)
        print("模式：%s" % ("演练（只打印不发送）" if self.dry_run else "真实发送"))
        print("")
        print("在 NapCat 的 WebUI 里这样填：")
        print("  HTTP 服务：开启，端口 3000")
        print("  HTTP 上报地址：http://%s:%d%s" % (self.host, self.port, self.path))
        print("")
        print("然后用你的主 QQ 加小号为好友，直接发消息试试。按 Ctrl+C 退出。")
        print("=" * 60)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止")
        finally:
            server.server_close()
