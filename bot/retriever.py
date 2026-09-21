"""真实语料检索：从聊天记录里找出"和这句话最像的对话"，喂给模型当语气参考。

用轻量 BM25（字符 + 二元组 + 英文单词），不依赖任何第三方库，也不联网。
"""

from __future__ import annotations

import json
import math
import random
import re
from collections import Counter
from pathlib import Path

LATIN_RE = re.compile(r"[A-Za-z0-9_]+")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text):
    text = (text or "").strip().lower()
    if not text:
        return []
    tokens = LATIN_RE.findall(text)
    chars = [char for char in text if CJK_RE.match(char)]
    tokens.extend(chars)
    for index in range(len(chars) - 1):
        tokens.append(chars[index] + chars[index + 1])
    return tokens


def pair_query_text(pair):
    context = pair.get("context") or []
    return " ".join(str(item.get("text", "")) for item in context if isinstance(item, dict))


def pair_context_text(pair):
    context = pair.get("context") or []
    parts = ["%s：%s" % (item.get("sender", "对方"), item.get("text", "")) for item in context if isinstance(item, dict)]
    return " / ".join(parts)


class Retriever:
    def __init__(self, pairs, k=4, max_pairs=8000):
        pairs = list(pairs or [])
        if len(pairs) > max_pairs:
            step = len(pairs) / float(max_pairs)
            pairs = [pairs[int(index * step)] for index in range(max_pairs)]
        self.pairs = pairs
        self.k = k
        self.docs = [tokenize(pair_query_text(pair)) for pair in self.pairs]
        self.doc_freq = Counter()
        for doc in self.docs:
            for token in set(doc):
                self.doc_freq[token] += 1
        self.total = len(self.docs)
        lengths = [len(doc) for doc in self.docs]
        self.avg_len = (sum(lengths) / float(self.total)) if self.total else 1.0

    def search(self, query, k=None, exclude=None, pool=8):
        """检索和这句话最像的历史对话。

        注意：不要总把"最像的那一条"喂给模型——模型会直接照抄，导致同一个问题
        每次得到一模一样的回答（像在查表）。所以这里先取相似度最高的一小撮，
        再随机挑几条，并且排除最近刚用过的，让回答自然有变化。
        """
        limit = k or self.k
        tokens = tokenize(query)
        if not tokens or not self.total:
            return []
        exclude = exclude or set()
        k1, b = 1.5, 0.75
        scored = []
        for index, doc in enumerate(self.docs):
            if not doc:
                continue
            length = len(doc)
            counts = Counter(doc)
            score = 0.0
            for token in tokens:
                frequency = counts.get(token)
                if not frequency:
                    continue
                df = self.doc_freq.get(token, 0)
                idf = math.log(1.0 + (self.total - df + 0.5) / (df + 0.5))
                score += idf * (frequency * (k1 + 1)) / (
                    frequency + k1 * (1 - b + b * length / self.avg_len)
                )
            if score > 0:
                scored.append((score, index))
        scored.sort(key=lambda item: item[0], reverse=True)

        candidates = [
            item for item in scored[: max(pool, limit * 3)]
            if item[1] not in exclude
        ]
        # 用过的就不再用（哪怕是唯一命中的那条）：宁可这一轮不给参考语料，
        # 也不要让它照抄旧答案——不然同一个问题永远得到同一句回复。
        if not candidates:
            candidates = [item for item in scored if item[1] not in exclude]
        random.shuffle(candidates)
        results = []
        for score, index in candidates[:limit]:
            pair = dict(self.pairs[index])
            pair["score"] = round(score, 3)
            pair["index"] = index
            results.append(pair)
        return results

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "count": len(self.pairs), "pairs": self.pairs}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path, k=4, max_pairs=8000):
        path = Path(path)
        if not path.exists():
            return cls([], k=k, max_pairs=max_pairs)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return cls([], k=k, max_pairs=max_pairs)
        pairs = payload.get("pairs") if isinstance(payload, dict) else payload
        return cls(pairs or [], k=k, max_pairs=max_pairs)

    def format_examples(self, hits, limit=4):
        """渲染成"对方说了什么 -> 本人怎么回"的对照，给模型当语气参考。

        注意：这里的"对方"指正在跟本人聊天的人（也就是你），
        "本人"才是要模仿的那个人。别在这里再套一层发送者名字，否则会变成
        "对方：我：xxx"这种读不通的东西。
        """
        lines = []
        for hit in hits[:limit]:
            context_lines = []
            for item in hit.get("context") or []:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text", "")).strip()
                if text:
                    context_lines.append("对方：" + text)
            reply = str(hit.get("reply", "")).strip()
            if not reply:
                continue
            if not context_lines:
                context_lines.append("对方：（直接开口）")
            lines.append("\n".join(context_lines) + "\n本人：" + reply)
        return "\n---\n".join(lines)
