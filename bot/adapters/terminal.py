"""终端模拟：不开微信也能验证人设像不像。"""

from __future__ import annotations

import time

from .base import Adapter, Incoming
from ..retriever import pair_context_text


class TerminalAdapter(Adapter):
    name = "终端"

    def __init__(self, cfg, engine, fast=False):
        super().__init__(cfg, engine)
        self.fast = fast
        self.chat_id = "terminal"
        self.sender = "朋友"
        self.last_message = ""

    def fetch(self):
        return []

    def send(self, chat_id, text):
        pass

    def handle(self, item):
        plan = self.engine.plan(item.chat_id, item.sender, item.text)
        if not plan.send or not plan.chunks:
            print("（%s 这次没回：%s）" % (self.engine.name, plan.reason))
            return
        for index, chunk in enumerate(plan.chunks):
            if not self.fast:
                time.sleep(min(plan.delays[index], 3.0))
            print("%s > %s" % (self.engine.name, chunk))

    def run(self, poll_interval=None):
        print("=" * 60)
        print("终端试聊模式：你扮演对方，%s 用人设回你" % self.engine.name)
        print("命令：/prompt 看人设提示词 ｜ /examples [某句话] 看检索到的语料 ｜ /facts 看长期记忆 ｜ /debug 看完整上下文 ｜ /exit 退出")
        print("=" * 60)
        while True:
            try:
                line = input("\n%s > " % self.sender)
            except (EOFError, KeyboardInterrupt):
                print("\n（结束）")
                break
            line = line.strip()
            if not line:
                continue
            if line in ("/exit", "/quit", "exit", "quit"):
                break
            if line == "/prompt":
                print(self.engine.system_prompt)
                continue
            if line == "/examples":
                line = "/examples " + self.last_message
            if line.startswith("/examples "):
                query = line[len("/examples ") :].strip()
                if not self.engine.retriever:
                    print("（这个人格没有加载语料）")
                    continue
                if not query:
                    print("（先随便说句话，或者写成 /examples 你想问的话）")
                    continue
                hits = self.engine.retriever.search(query, 4)
                print("（用「%s」检索到的真实语料）" % query)
                if not hits:
                    print("  （没检索到相似语料，说明这句话没有历史相似场景）")
                for hit in hits:
                    print(
                        "  相似度 %.2f｜%s  →  %s"
                        % (hit.get("score", 0), pair_context_text(hit), hit.get("reply"))
                    )
                continue
            if line == "/facts":
                if not self.engine.memory:
                    print("（没有开启记忆）")
                    continue
                facts = self.engine.memory.facts(self.chat_id, 20)
                print("（长期记忆：%s）" % ("；".join(facts) if facts else "还没有"))
                continue
            if line.startswith("/name "):
                self.sender = line[6:].strip() or self.sender
                print("（对方名字设为 %s）" % self.sender)
                continue
            if line == "/debug":
                self.engine.debug = not self.engine.debug
                print("（调试输出：%s）" % ("开" if self.engine.debug else "关"))
                continue
            self.last_message = line
            self.handle(Incoming(chat_id=self.chat_id, sender=self.sender, text=line))
