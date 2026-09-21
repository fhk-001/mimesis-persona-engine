"""本地风格统计画像：不需要大模型，纯统计出"这个人怎么说话"。

统计维度：句长、标点习惯、口头禅、语气词、表情、连发习惯、活跃时段、回复速度。
"""

from __future__ import annotations

import re
from collections import Counter
from statistics import median

EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\u2b00-\u2bff\u2190-\u21ff]"
)
WE_CHAT_EMOJI_RE = re.compile(r"\[[^\[\]]{1,6}\]")
KAOMOJI_RE = re.compile(
    r"([（(][^（）()\n]{0,8}[）)])|(\^[_\-]?\^)|(:[\)\(DdPp])|([Tt]_[Tt])|([qQ][wW][qQ])|(orz)|(>_<)|(●'◡'●)"
)
PURE_PUNCT_RE = re.compile(r"^[\s\W_]+$", re.UNICODE)
END_PUNCT = "。！？!?~～…"
# 提取口头禅时要剪掉的边缘字符：空白、标点、括号、表情标记
EDGE_CHARS = " \t\u3000，。！？!?~～…、；;：:,.·\"'“”‘’()（）[]【】{}<>《》|/\\-_=+*&#@$%^`"
# 这些字是语法助词，不可能当一段话的开头，命中说明是断词碎片
GRAM_STOP_PREFIX = ("的", "了", "地", "得", "着", "之")
# 纯功能词，不可能是口头禅
GRAM_STOPWORDS = {
    "什么", "怎么", "这个", "那个", "就是", "不是", "没有", "可以", "一个",
    "现在", "然后", "因为", "所以", "但是", "如果", "还是", "已经", "一下",
    "时候", "我们", "你们", "他们", "自己", "知道", "觉得", "应该", "可能",
}
WORD_LIKE_RE = re.compile(r"[^\w\u4e00-\u9fff]", re.UNICODE)
# 汉字：用来把截图识别出来的纯字母/数字串（"III"、"cos 0"）挡在口头禅之外
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 这些是聊天软件的界面用词：识别截图时经常被带进来，不能当口头禅
UI_WORDS = (
    "聊天记录", "聊天", "记录", "设置", "朋友圈", "联系人", "通讯录", "公众号",
    "小程序", "钱包", "收藏", "相册", "账单", "视频号", "群聊", "好友", "消息",
    "免打扰", "置顶", "备注", "扫一扫", "二维码", "输入", "发送",
)
MODAL_WORDS = (
    "哈哈",
    "哈哈哈",
    "嘿嘿",
    "嘻嘻",
    "嗯",
    "嗯嗯",
    "哦",
    "噢",
    "啊",
    "呀",
    "吧",
    "啦",
    "嘛",
    "呢",
    "诶",
    "唉",
    "哟",
    "哇",
    "呵",
    "嗷",
    "唔",
    "emmm",
    "emm",
)


def _ngrams(text, sizes=(2, 3, 4, 5)):
    text = text.replace("\n", " ")
    for size in sizes:
        for index in range(len(text) - size + 1):
            yield text[index : index + size]


def _clean_gram(raw):
    """把 n-gram 边缘的空白和标点剪掉，"真的假的 " -> "真的假的"。"""
    return raw.strip(EDGE_CHARS)


def _valid_gram(gram):
    """只保留能当口头禅的连续短句：不含空格、标点、括号、表情。"""
    if len(gram) < 2:
        return False
    # 必须含汉字：截图识别出来的 "III"、"IIIII"、"cos 0" 这类纯字母/数字串
    # 不是口头禅，写进提示词反而会让模型学着一串符号刷屏。
    if not CJK_RE.search(gram):
        return False
    if PURE_PUNCT_RE.match(gram):
        return False
    if WORD_LIKE_RE.search(gram):
        return False
    if gram in GRAM_STOPWORDS:
        return False
    for word in UI_WORDS:
        if word in gram:
            return False
    if gram[0] in GRAM_STOP_PREFIX:
        return False
    return True


def _catchphrases(texts, min_docs=2, top=20):
    doc_freq = Counter()
    total = Counter()
    for text in texts:
        seen = set()
        for raw in _ngrams(text):
            gram = _clean_gram(raw)
            if not _valid_gram(gram):
                continue
            total[gram] += 1
            if gram not in seen:
                seen.add(gram)
                doc_freq[gram] += 1
    ranked = sorted(
        (g for g, c in doc_freq.items() if c >= min_docs and total[g] >= min_docs),
        key=lambda g: (-(doc_freq[g] * (len(g) - 1) + total[g] * 0.1), -total[g], g),
    )
    kept = []
    for gram in ranked:
        if any(gram in other for other, _ in kept):
            continue
        kept.append((gram, doc_freq[gram]))
        if len(kept) >= top:
            break
    return kept


def analyze(records, target):
    messages = [r for r in records if r.sender == target and r.kind == "text" and r.text.strip()]
    texts = [m.text.strip() for m in messages]
    total = len(texts)
    if total == 0:
        return {"target": target, "messages": 0}

    lengths = [len(t) for t in texts]
    ordered = sorted(lengths)

    def ratio(count, base=None):
        base = base or total
        return round(count / base, 3) if base else 0.0

    def ends_with(marks):
        return sum(1 for t in texts if t and t[-1] in marks)

    def contains_any(needles):
        return sum(1 for t in texts if any(n in t for n in needles))

    punct = {
        "period": ratio(ends_with("。")),
        "question": ratio(ends_with("？?")),
        "exclaim": ratio(ends_with("！!")),
        "tilde": ratio(contains_any(("~", "～"))),
        "ellipsis": ratio(contains_any(("…", "...", "。。。", "。。"))),
        "no_end_punct": ratio(sum(1 for t in texts if t and t[-1] not in END_PUNCT)),
        "comma": ratio(contains_any(("，", ","))),
    }

    modal = Counter()
    for text in texts:
        for word in MODAL_WORDS:
            count = text.count(word)
            if count:
                modal[word] += count
    # "哈哈" 会命中 "哈哈哈"，去掉重复计数
    for long_form, short_form in (("哈哈哈", "哈哈"), ("嘿嘿", "嘿"), ("嗯嗯", "嗯"), ("emmm", "emm")):
        if modal.get(long_form) and modal.get(short_form):
            modal[short_form] = max(0, modal[short_form] - modal[long_form] * long_form.count(short_form))

    emoji_count = sum(len(EMOJI_RE.findall(t)) for t in texts)
    wechat_emoji = Counter()
    for text in texts:
        wechat_emoji.update(WE_CHAT_EMOJI_RE.findall(text))
    kaomoji_count = sum(1 for t in texts if KAOMOJI_RE.search(t))

    hour_hist = Counter(r.ts.hour for r in messages if r.ts)

    latency = []
    for index in range(1, len(records)):
        previous, current = records[index - 1], records[index]
        if previous.ts and current.ts and current.sender == target and previous.sender != target:
            delta = (current.ts - previous.ts).total_seconds()
            if 0 <= delta <= 3600:
                latency.append(delta)

    runs, run = [], 0
    for record in records:
        if record.sender == target and record.kind == "text":
            run += 1
        else:
            if run:
                runs.append(run)
            run = 0
    if run:
        runs.append(run)

    starts = Counter(t[:2] for t in texts if len(t) >= 2 and _valid_gram(_clean_gram(t[:2])))
    ends = Counter(t[-2:] for t in texts if len(t) >= 2 and _valid_gram(_clean_gram(t[-2:])))
    latin_ratio = ratio(sum(1 for t in texts if re.search(r"[A-Za-z]", t)))
    digits_ratio = ratio(sum(1 for t in texts if re.search(r"\d", t)))
    sentence_split = [len(re.split(r"[，。！？!?~…\n]+", t)) - 1 for t in texts]

    return {
        "target": target,
        "messages": total,
        "chars": sum(lengths),
        "avg_len": round(sum(lengths) / total, 1),
        "median_len": median(ordered),
        "p90_len": ordered[min(total - 1, int(total * 0.9))],
        "short_ratio": ratio(sum(1 for n in lengths if n <= 6)),
        "long_ratio": ratio(sum(1 for n in lengths if n >= 30)),
        "punct": punct,
        "modal_words": modal.most_common(12),
        "top_catchphrases": _catchphrases(texts),
        "emoji_per_message": round(emoji_count / total, 2),
        "emoji_ratio": ratio(sum(1 for t in texts if EMOJI_RE.search(t))),
        "wechat_emoji_top": wechat_emoji.most_common(8),
        "kaomoji_ratio": ratio(kaomoji_count),
        "bursts": {
            "mean": round(sum(runs) / len(runs), 2) if runs else 1.0,
            "max": max(runs) if runs else 1,
            "multi_ratio": ratio(sum(1 for r in runs if r >= 2), len(runs) or 1),
        },
        "active_hours": hour_hist.most_common(6),
        "reply_seconds_median": round(median(latency), 1) if latency else None,
        "latin_ratio": latin_ratio,
        "digits_ratio": digits_ratio,
        "clauses_per_message": round(sum(sentence_split) / total, 2),
        "top_starts": [s for s, _ in starts.most_common(6)],
        "top_ends": [e for e, _ in ends.most_common(6)],
    }


def report_text(report):
    """把统计结果渲染成给模型（和人）看的可读文本。"""
    if not report or not report.get("messages"):
        return "（没有可用语料）"
    punct = report["punct"]
    lines = [
        "样本消息数：%d 条，共 %d 字" % (report["messages"], report.get("chars", 0)),
        "句长：平均 %.1f 字，中位数 %s 字，90 分位 %s 字；短句(<=6字)占比 %.0f%%，长句(>=30字)占比 %.0f%%"
        % (
            report["avg_len"],
            report["median_len"],
            report["p90_len"],
            report["short_ratio"] * 100,
            report["long_ratio"] * 100,
        ),
        "标点：句号结尾 %.0f%%，问号结尾 %.0f%%，感叹号结尾 %.0f%%，不带结束标点 %.0f%%，用逗号 %.0f%%，用波浪号 %.0f%%，用省略号 %.0f%%"
        % (
            punct["period"] * 100,
            punct["question"] * 100,
            punct["exclaim"] * 100,
            punct["no_end_punct"] * 100,
            punct["comma"] * 100,
            punct["tilde"] * 100,
            punct["ellipsis"] * 100,
        ),
    ]
    if report["modal_words"]:
        lines.append(
            "语气词/笑声：" + "、".join("%s×%d" % (w, c) for w, c in report["modal_words"][:8])
        )
    if report["top_catchphrases"]:
        lines.append(
            "高频口头禅：" + "、".join('"%s"(出现于%d条消息)' % (g, c) for g, c in report["top_catchphrases"][:10])
        )
    emoji_bits = []
    if report["emoji_per_message"]:
        emoji_bits.append("每条约 %.2f 个表情" % report["emoji_per_message"])
    if report["wechat_emoji_top"]:
        emoji_bits.append(
            "常用微信表情：" + "、".join("%s×%d" % (e, c) for e, c in report["wechat_emoji_top"][:5])
        )
    if report["kaomoji_ratio"]:
        emoji_bits.append("颜文字出现率 %.0f%%" % (report["kaomoji_ratio"] * 100))
    if emoji_bits:
        lines.append("表情：" + "；".join(emoji_bits))
    lines.append(
        "连发习惯：平均一轮连发 %.2f 条，最多 %d 条，出现连发(>=2条)的比例 %.0f%%"
        % (
            report["bursts"]["mean"],
            report["bursts"]["max"],
            report["bursts"]["multi_ratio"] * 100,
        )
    )
    if report["active_hours"]:
        lines.append(
            "活跃时段：" + "、".join("%d点(%d条)" % (h, c) for h, c in report["active_hours"])
        )
    if report["reply_seconds_median"] is not None:
        lines.append("回复间隔中位数：%.0f 秒" % report["reply_seconds_median"])
    lines.append(
        "其他：中英混用 %.0f%%，含数字 %.0f%%，平均每条 %.2f 个停顿标点（越低越爱一口气说完）"
        % (
            report["latin_ratio"] * 100,
            report["digits_ratio"] * 100,
            report["clauses_per_message"],
        )
    )
    if report["top_starts"]:
        lines.append("常见开头两字：" + "、".join(report["top_starts"]))
    if report["top_ends"]:
        lines.append("常见结尾两字：" + "、".join(report["top_ends"]))
    return "\n".join(lines)
