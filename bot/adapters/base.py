"""接入层基类：统一"拉消息 -> 生成 -> 发消息"的循环。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Incoming:
    chat_id: str
    sender: str
    text: str
    is_group: bool = False
    key: str = ""
    raw: object = None
    extra: dict = field(default_factory=dict)


class Adapter:
    name = "adapter"

    def __init__(self, cfg, engine):
        self.cfg = cfg
        self.engine = engine

    # ---- 子类实现 ----
    def fetch(self):
        """返回这一轮收到的新消息列表（Incoming）。"""
        raise NotImplementedError

    def send(self, chat_id, text):
        raise NotImplementedError

    # ---- 通用流程 ----
    def handle(self, item):
        dry_run = bool(self.cfg.get("behavior", {}).get("dry_run", True))
        try:
            plan = self.engine.plan(item.chat_id, item.sender, item.text, is_group=item.is_group)
        except Exception as exc:  # noqa: BLE001 - 单条消息出错不能拖垮整个机器人
            print("[%s] 生成回复失败：%s" % (self.name, exc))
            return
        if not plan.send or not plan.chunks:
            print("[%s] 收到「%s」的消息，但选择不回：%s" % (self.name, item.sender, plan.reason))
            return
        if dry_run:
            gaps = " / ".join("%.1fs" % delay for delay in plan.delays)
            print("[演练模式] 本来要回 %s：%s" % (item.sender, " ｜ ".join(plan.chunks)))
            print("            发送间隔：%s（第一条约 %.1f 秒，后面几条快一些）"
                  % (gaps, plan.delays[0] if plan.delays else 0))
            print("            （确认没问题后，把 config.json 里 behavior.dry_run 改成 false 就会真的发出去）")
            return
        for chunk, delay in zip(plan.chunks, plan.delays):
            time.sleep(delay)
            try:
                self.send(item.chat_id, chunk)
            except Exception as exc:  # noqa: BLE001
                print("[%s] 发送失败：%s" % (self.name, exc))
                return
            print("[%s] -> %s：%s" % (self.name, item.sender, chunk))

    def run(self, poll_interval=None):
        interval = poll_interval or self.cfg.get("wechat", {}).get("poll_interval", 1.2)
        dry_run = bool(self.cfg.get("behavior", {}).get("dry_run", True))
        print("=" * 60)
        print("人格：%s" % self.engine.name)
        print("模式：%s" % ("演练（只打印不发送）" if dry_run else "真实发送"))
        print("按 Ctrl+C 退出")
        print("=" * 60)
        while True:
            try:
                items = self.fetch()
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001 - 拉取失败时等一会儿重试
                print("[%s] 拉取出错：%s" % (self.name, exc))
                time.sleep(3)
                continue
            for item in items or []:
                self.handle(item)
            time.sleep(interval)
