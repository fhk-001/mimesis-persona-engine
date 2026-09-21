"""个人微信接入：用 wxauto 操作电脑版微信（Windows）。

安装（wxauto 不在 PyPI 上，要从 GitHub 装）：
    python -m pip install git+https://github.com/cluic/wxauto.git

版本要求（2026-09 查证的官方说明）：
    wxauto 官方支持的是【微信电脑版 3.9.X】。
    新版 4.x 客户端（进程名 Weixin.exe，装在 C:\\Program Files\\Tencent\\Weixin）
    不被支持——付费版 wxautox 也只写"3.9.8+"。
    官方 README 里给了 3.9 客户端下载（百度网盘，提取码 vsmj）：
    https://pan.baidu.com/s/1FvSw0Fk54GGvmQq8xSrNjA?pwd=vsmj

想让它成为"微信里的一个好友"，需要：
    1. 一个小号（第二个微信号）当机器人的身体
    2. 电脑上装微信 3.9.X，用这个小号登录并保持在线（可以和你自己用的 4.x 共存，
       因为两者安装目录不同）
    3. 机器人在这台电脑上跑，自动回复发给小号的消息
    然后你在自己的微信里点小号的头像就能聊天。

风险：自动化个人微信属于非官方用法，有被限制或封号的风险。
请用小号、降低频率（config.json 里的 max_replies_per_minute / ignore_probability 就是干这个的）。
企业微信是官方合规通道，见 bot/adapters/wecom.py。
"""

from __future__ import annotations

import hashlib
import time

from .base import Adapter, Incoming


def _text_of(item):
    if item is None:
        return "", "", "friend"
    if isinstance(item, str):
        return item, "", "friend"
    if isinstance(item, dict):
        return (
            str(item.get("content") or item.get("text") or item.get("msg") or ""),
            str(item.get("sender") or item.get("who") or ""),
            str(item.get("type") or "friend"),
        )
    content = getattr(item, "content", None) or getattr(item, "text", "") or ""
    sender = getattr(item, "sender", "") or getattr(item, "who", "") or ""
    kind = getattr(item, "type", "") or "friend"
    return str(content), str(sender), str(kind)


class WxautoAdapter(Adapter):
    name = "微信"

    def __init__(self, cfg, engine):
        super().__init__(cfg, engine)
        try:
            from wxauto import WeChat  # type: ignore
        except ImportError as exc:
            raise SystemExit(
                "没有安装 wxauto（它不在 PyPI 上，必须从 GitHub 装）：\n"
                "    python -m pip install git+https://github.com/cluic/wxauto.git\n"
                "另外注意：wxauto 只支持【微信电脑版 3.9.X】，新版 4.x 客户端不支持。\n"
                "（原始错误：%s）" % exc
            )
        try:
            self.wx = WeChat()
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(
                "启动微信自动化失败：%s\n"
                "请检查：1) 微信 3.9.X 是否已用【小号】登录 2) 窗口是否被最小化到托盘\n"
                "        3) 是不是装的新版 4.x 客户端（wxauto 不支持，需要另装 3.9.X）"
                % exc
            )
        self.seen = set()
        # 机器人自己的微信昵称：它发出去的消息不能被当成"别人说的"
        self.self_names = set(cfg.get("wechat", {}).get("self_names") or [])
        self.self_names.update({engine.name, "我", "self"})
        self._group_cache = {}
        self.use_all_new = hasattr(self.wx, "GetAllNewMessage")
        self.listening = []
        if not self.use_all_new:
            self._setup_listening()

    def _setup_listening(self):
        """老版 wxauto 需要先 AddListenChat。"""
        names = list(self.cfg.get("behavior", {}).get("whitelist") or [])
        if not names:
            print(
                "[微信] 你用的 wxauto 版本没有 GetAllNewMessage，需要在 config.json 的\n"
                "       behavior.whitelist 里列出要监听的聊天（备注名），才能开始监听。"
            )
            return
        for name in names:
            try:
                self.wx.AddListenChat(who=name)
                self.listening.append(name)
                print("[微信] 开始监听：%s" % name)
            except Exception as exc:  # noqa: BLE001
                print("[微信] 监听 %s 失败：%s" % (name, exc))

    def _raw_messages(self):
        if self.use_all_new:
            data = self.wx.GetAllNewMessage()
            if isinstance(data, dict):
                return data
            if isinstance(data, list):
                result = {}
                for item in data:
                    _, _, _ = _text_of(item)
                    result.setdefault("未知会话", []).append(item)
                return result
            return {}
        data = self.wx.GetListenMessage() if hasattr(self.wx, "GetListenMessage") else {}
        if isinstance(data, dict):
            result = {}
            for chat, items in data.items():
                name = getattr(chat, "who", None) or str(chat)
                result[name] = items if isinstance(items, list) else [items]
            return result
        return {}

    @staticmethod
    def _digest(chat_id, sender, text):
        raw = "%s|%s|%s" % (chat_id, sender, text)
        return hashlib.md5(raw.encode("utf-8", "replace")).hexdigest()

    def fetch(self):
        incoming = []
        try:
            raw = self._raw_messages()
        except Exception as exc:  # noqa: BLE001
            print("[微信] 读取新消息失败：%s" % exc)
            return []
        for chat_name, items in raw.items():
            if isinstance(items, (str, dict)):
                items = [items]
            for item in items or []:
                text, sender, kind = _text_of(item)
                text = text.strip()
                if not text:
                    continue
                if kind.lower() in ("self", "mine", "自己"):
                    continue
                if sender and sender in self.self_names:
                    continue
                is_group = bool(sender) and sender != chat_name
                display_sender = sender or chat_name
                key = self._digest(chat_name, display_sender, text)
                if key in self.seen:
                    continue
                self.seen.add(key)
                if len(self.seen) > 5000:
                    self.seen = set(list(self.seen)[-2000:])
                incoming.append(
                    Incoming(
                        chat_id=chat_name,
                        sender=display_sender,
                        text=text,
                        is_group=is_group,
                        key=key,
                        raw=item,
                    )
                )
        return incoming

    def send(self, chat_id, text):
        if hasattr(self.wx, "SendMsg"):
            self.wx.SendMsg(msg=text, who=chat_id)
        elif hasattr(self.wx, "ChatWith"):
            self.wx.ChatWith(who=chat_id)
            time.sleep(0.3)
            self.wx.SendMsg(text)
        else:
            raise RuntimeError("当前 wxauto 版本没有可用的发送接口")

    def check(self):
        """自检：能不能读到会话、能不能找到微信窗口。"""
        info = {}
        try:
            info["会话数量"] = len(self._raw_messages())
        except Exception as exc:  # noqa: BLE001
            info["会话读取失败"] = str(exc)
        info["GetAllNewMessage"] = self.use_all_new
        info["监听模式"] = self.listening or "未使用"
        for key, value in info.items():
            print("  %s：%s" % (key, value))
        return info
