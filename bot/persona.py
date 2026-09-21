"""性格画像：本地统计 + 大模型深度分析 -> persona.json + 系统提示词。"""

from __future__ import annotations

import json
import random
from pathlib import Path

from . import style
from .llm import extract_json

ANALYSIS_SYSTEM = """你是一位资深的语言风格与人格分析师。
用户会给你某个人的聊天记录统计报告和真实语料样本，请你只根据证据，提炼出这个人的"聊天人格档案"。
要求：
1. 只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块。
2. 所有结论必须有语料支撑，不要凭空想象；证据不足就写得保守一些。
3. 只描述"这个人说话和处事的风格"，不要评价好坏，不要臆测隐私。
4. 这是亲近关系（情侣或好朋友）的日常聊天。"你滚""我恨你""服了""太歹毒了"这类话基本是玩笑和撒娇，
  不要解读成攻击性、戾气重的性格。主基调要写成"亲昵、松弛、爱撒娇、偶尔嘴硬"，而不是"爱怼人""火药味重"。
5. 如果用户给了这个人的背景信息（年龄、身份、关系），以背景为准；口吻要符合这个身份，不要用侮辱性的词描述他。
6. 【最重要】你要提炼的是"表达方式"，不是"她说过哪些句子"。
   禁止把输出写成金句清单或者标准答案表。真正要抓住的是：
   她怎么起话头、怎么接话、怎么拒绝、怎么表达情绪、怎么结束话题、句子的长短和节奏、
   以及"同样的意思她会用什么样的口吻说出来"。这些要能迁移到任何新话题上。

JSON 字段：
{
    "name": "人名/昵称",
    "one_liner": "一句话概括这个人给人的感觉",
    "traits": ["3-6 个性格关键词，每个后面用 8-20 字说明依据"],
    "tone": "整体语气：例如 松弛随意 / 温柔耐心 / 毒舌直接 / 冷淡话少",
    "values": ["在意的点、判断事情的标准，2-5 条"],
    "humor": "幽默方式，例如 自嘲 / 冷幽默 / 爱用夸张吐槽；没有就写'不明显'",
    "emotional_patterns": "情绪表达模式：生气、开心、敷衍、关心别人时分别怎么表现",
  "expression_rules": [
    "【最重要的字段】可迁移的表达规则，每条写成「什么情况 -> 她会怎么表达」。",
    "要写抽象的行为方式，不要引用原句。例如：",
    "  被问在干嘛 -> 先丢一句自己的状态（常常是几个字），偶尔反问'你呢'，很少展开解释",
    "  被约出去 -> 常常先拒绝，再补一句很轻的理由（懒得动/没钱/不想出门），不道歉也不解释",
    "  被夸 -> 嘴上不接，改用一句玩笑或转移话题，但会继续聊下去",
    "  心情不好 -> 不直接说难过，改用短句、语气词和'算了''不想说'带过",
    "写 6-12 条，覆盖：开场、接话、拒绝、答应、抱怨、开心、敷衍、被追问、收尾"
  ],
  "reply_rhythm": "回复节奏：一句话多长、多久发一条、会不会拆成几条连着发、标点习惯（要写成可直接执行的规则）",
    "loves": ["喜欢聊的话题"],
    "avoids": ["会回避、敷衍或不想聊的话题"],
    "speech_habits": ["具体到可执行的语言习惯，例如 '几乎不用句号，喜欢用空格或波浪号收尾'"],
      "catchphrases": ["真正有辨识度的口头禅，最多 5 个；宁缺毋滥，不要把普通高频词写进来"],
    "boundaries": ["这个人会明确拒绝或反感的事情"],
    "reply_length": "回复长度习惯：多数回复几个字到几十字？会不会连发多条？",
    "relationship_notes": "对不同关系的人（亲密/普通/陌生人）态度差异",
  "example_replies": [
    {"context": "对方说的话", "reply": "这个人的真实回复", "why": "这条体现了什么表达策略"}
  ]
}"""

# 第二轮：专门做"表达策略抽取"，把真实问答对抽象成可迁移的规则
PATTERN_SYSTEM = """你是语言风格分析师，擅长从对话里抽象出"表达策略"。

给你若干条真实问答对（对方说了什么 → 她怎么回）。请你**不要复述她说了什么**，
而要总结"她在这种情况下是怎么表达的"。

每条输出一行，格式固定：
<对方做什么> -> <她的表达方式（抽象描述）>

要求：
1. 描述的是行为方式，不引用原句，不提具体人名、游戏名、商品名。
2. 写清楚"用什么口吻、说多少、先说什么后说什么、有没有解释/道歉/反问"。
3. 8-15 行，覆盖不同场景（打招呼、闲聊、被约、被问、被夸、被吐槽、抱怨、结束话题等）。
4. 只输出这些行，不要别的话。

示例格式（仅示范写法）：
对方问在干嘛 -> 先丢一句自己的状态，常常只有几个字，偶尔反问回去，不展开解释
对方约她出去 -> 先拒绝，再补一句很轻的理由，不道歉也不多解释
对方吐槽她 -> 用短句怼回去或顺势自嘲，但会继续聊下去，不会真的生气"""


def _sample_texts(records, target, limit=160, seed=7):
    texts = [r.text.strip() for r in records if r.sender == target and r.kind == "text" and r.text.strip()]
    if len(texts) <= limit:
        return texts
    rng = random.Random(seed)
    picked = rng.sample(texts, limit)
    return picked


def heuristic_persona(report, name):
    """完全不调用大模型，仅凭统计得出可用的基础画像。"""
    if not report or not report.get("messages"):
        return {"name": name, "one_liner": "（语料不足，无法提炼）", "source": "heuristic"}
    avg = report["avg_len"]
    punct = report["punct"]
    traits, habits = [], []

    if avg <= 8:
        traits.append("说话很短，常用几个字解决问题（平均 %.1f 字）" % avg)
    elif avg >= 25:
        traits.append("愿意展开说，话比较密（平均 %.1f 字）" % avg)
    else:
        traits.append("说话长度适中（平均 %.1f 字）" % avg)
    if report["short_ratio"] >= 0.5:
        traits.append("大量短句回复，情绪和态度都藏在短句里")
    if punct["no_end_punct"] >= 0.6:
        habits.append("几乎不用句号，句子说完就发")
    if punct["tilde"] >= 0.15:
        habits.append("喜欢用波浪号收尾，语气偏软")
    if punct["ellipsis"] >= 0.15:
        habits.append("常用省略号，话留一半")
    if punct["exclaim"] >= 0.1:
        habits.append("情绪外放，爱用感叹号")
    if punct["question"] >= 0.15:
        traits.append("习惯反问、追问，聊天时更爱把话抛回去")
    if report["kaomoji_ratio"] >= 0.05 or report["emoji_ratio"] >= 0.15:
        traits.append("靠表情/颜文字传递语气，文字本身偏干")
    if report["bursts"]["mean"] >= 1.8:
        traits.append("喜欢连发消息（平均一轮 %.1f 条）" % report["bursts"]["mean"])
    if report["latin_ratio"] >= 0.1:
        habits.append("中英文混着用")
    if report["clauses_per_message"] <= 0.5:
        habits.append("一条消息只表达一个意思，很少用逗号串长句")

    modal_words = [w for w, _ in report.get("modal_words", [])][:6]
    if modal_words:
        habits.append("高频语气词：" + "、".join(modal_words))
    if report.get("active_hours"):
        hours = [h for h, _ in report["active_hours"][:3]]
        traits.append("主要在 " + "、".join("%d点" % h for h in sorted(hours)) + " 前后活跃")
    if report.get("reply_seconds_median") is not None:
        seconds = report["reply_seconds_median"]
        if seconds <= 30:
            traits.append("回复很快，基本是秒回")
        elif seconds >= 600:
            traits.append("回复不急，常常隔很久才回")

    return {
        "name": name,
        "one_liner": "%s：%s，%s" % (name, traits[0] if traits else "风格待观察", habits[0] if habits else "语句习惯待观察"),
        "traits": traits,
        "tone": "偏" + ("轻松随意" if punct["tilde"] or report["kaomoji_ratio"] else "直接简洁"),
        "values": [],
        "humor": "未分析（需要大模型）",
        "emotional_patterns": "未分析（需要大模型）",
        "loves": [],
        "avoids": [],
        "speech_habits": habits,
        "catchphrases": [g for g, _ in report.get("top_catchphrases", [])][:10],
        "boundaries": [],
        "reply_length": "平均 %.1f 字，短句占比 %.0f%%" % (avg, report["short_ratio"] * 100),
        "relationship_notes": "",
        "example_replies": [],
        "source": "heuristic",
    }


def analyze_with_llm(llm, report, records, target, name, example_pairs=None):
    samples = _sample_texts(records, target)
    if not samples:
        return None
    lines = ["【统计报告】", style.report_text(report), "", "【随机抽取的真实发言样本】"]
    lines.extend(samples)
    if example_pairs:
        lines.append("")
        lines.append("【真实对话片段（对方=跟他聊天的人，本人=这个人自己）】")
        for pair in example_pairs[:20]:
            context = " / ".join(
                str(item.get("text", "")).strip()
                for item in pair.get("context", [])
                if isinstance(item, dict) and str(item.get("text", "")).strip()
            )
            if not context:
                continue
            lines.append("对方：%s" % context)
            lines.append("本人：%s" % pair.get("reply", ""))
    lines.append("")
    lines.append("请分析这个人（%s）并输出 JSON 档案。" % name)
    messages = [
        {"role": "system", "content": ANALYSIS_SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]
    try:
        # 用推理模型时，思考过程也占 token：给少了会直接返回空内容
        raw = llm.chat(messages, temperature=0.4, json_mode=True, max_tokens=6000)
    except Exception as exc:  # noqa: BLE001 - 大模型失败时退回本地画像
        print("[persona] 大模型分析失败，改用本地统计画像：%s" % exc)
        return None
    data = extract_json(raw)
    if not isinstance(data, dict):
        print("[persona] 大模型输出无法解析为 JSON，改用本地统计画像")
        return None

    # 第二轮：把真实问答对抽象成"表达策略"，避免只学到具体句子
    patterns = extract_expression_patterns(llm, example_pairs or [])
    if patterns:
        existing = [str(item) for item in (data.get("expression_rules") or [])]
        merged = list(existing)
        for rule in patterns:
            if rule not in merged:
                merged.append(rule)
        data["expression_rules"] = merged[:18]
        print("  已从真实对话里抽取出 %d 条表达策略" % len(patterns))

    data["source"] = "llm"
    return data


def extract_expression_patterns(llm, example_pairs, limit=14):
    """第二轮分析：把"对方说什么 → 她怎么回"抽象成可迁移的表达策略。

    这一步是"学她怎么表达"，而不是"记住她说过什么"。
    """
    blocks = []
    for pair in (example_pairs or [])[:limit]:
        if not isinstance(pair, dict):
            continue
        context = " / ".join(
            str(item.get("text", "")).strip()
            for item in pair.get("context", [])
            if isinstance(item, dict) and str(item.get("text", "")).strip()
        )
        reply = str(pair.get("reply", "")).strip()
        if context and reply:
            blocks.append("对方：%s\n本人：%s" % (context, reply))
    if not blocks:
        return []
    try:
        raw = llm.chat(
            [
                {"role": "system", "content": PATTERN_SYSTEM},
                {"role": "user", "content": "\n---\n".join(blocks)},
            ],
            temperature=0.3,
            max_tokens=2500,
        )
    except Exception as exc:  # noqa: BLE001 - 这一步失败不影响整体
        print("[persona] 表达策略抽取失败（不影响其它步骤）：%s" % exc)
        return []
    rules = []
    for line in (raw or "").splitlines():
        text = line.strip().lstrip("-*•").strip()
        if len(text) < 6:
            continue
        if "->" not in text and "→" not in text:
            continue
        text = text.replace("→", "->")
        if text not in rules:
            rules.append(text)
    return rules[:15]


LIST_FIELDS = (
    "traits", "values", "loves", "avoids", "speech_habits", "catchphrases", "boundaries",
    "expression_rules",
)
TEXT_FIELDS = (
    "name", "one_liner", "tone", "humor", "emotional_patterns", "reply_length",
    "relationship_notes", "reply_rhythm", "background",
)

# 这些词太通用，当口头禅塞进回复里只会答非所问
GENERIC_PHRASES = {
    "没有", "真的", "可以", "什么", "就是", "不是", "明天", "今天", "昨天",
    "现在", "然后", "但是", "所以", "还是", "已经", "知道", "觉得", "可能",
}
PRONOUN_STARTS = "你我他她它这那"
# 这类词在语料里可能是情侣间的玩笑，但绝不该被当成"口头禅"教给模型，
# 否则模型会不分场合地往外甩，看起来就像在骂人
HOSTILE_PHRASES = (
    "滚", "恨", "蠢", "弱智", "废物", "傻", "去死", "打死", "弄死",
    "有病", "神经病", "闭嘴", "恶心",
)
# 系统提示相关的词：这些东西出现在聊天里是"提示"，不是她说话的习惯
SYSTEM_WORDS = ("撤回", "消息", "提示", "通知", "拍了拍", "群聊", "系统")


def trim_catchphrases(values, limit=8):
    """口头禅只保留有辨识度的短句。

    大模型很容易把"没有""行""明天"这种日常高频词也列进口头禅，
    结果模型不分场合地硬塞，回复就变成答非所问。
    """
    kept = []
    for item in values:
        text = str(item).strip()
        if len(text) < 2:
            continue
        if text in GENERIC_PHRASES:
            continue
        if any(word in text for word in SYSTEM_WORDS):
            continue
        if "…" in text or "。。" in text:
            continue
        if any(bad in text for bad in HOSTILE_PHRASES):
            continue
        # "你先""你这"这种代词开头的碎片也不算口头禅
        if len(text) == 2 and text[0] in PRONOUN_STARTS:
            continue
        if text not in kept:
            kept.append(text)
        if len(kept) >= limit:
            break
    return kept


def merge_persona(base, extra):
    if not extra:
        return base
    merged = dict(base)
    llm_analysis = extra.get("source") == "llm"

    def as_list(value):
        if not value:
            return []
        if isinstance(value, str):
            return [value]
        return [str(item).strip() for item in value if str(item).strip()]

    def dedupe(values):
        result = []
        for item in values:
            if item and item not in result:
                result.append(item)
        return result

    for field in LIST_FIELDS:
        base_values = as_list(base.get(field))
        extra_values = as_list(extra.get(field))
        # 大模型的描述更具体，而且它已经看过统计报告；直接用它的，
        # 否则两套说法会重复（例如"说话很短"和"说话极简"同时出现在提示词里）。
        # 口头禅例外：本地统计的 n-gram 更靠谱，两边合并。
        if llm_analysis and extra_values:
            if field == "catchphrases":
                # 口头禅只用模型给的：本地 n-gram 经常把界面文字、歌名、
                # 作业里反复出现的词（"cos公式"之类）误当成口头禅。
                merged[field] = dedupe(extra_values)[:16]
            else:
                merged[field] = dedupe(extra_values)[:16]
        else:
            merged[field] = dedupe(base_values + extra_values)[:16]
    merged["catchphrases"] = trim_catchphrases(merged.get("catchphrases") or [])
    for field in TEXT_FIELDS:
        value = extra.get(field)
        if isinstance(value, str) and value.strip():
            merged[field] = value.strip()
    examples = list(base.get("example_replies") or [])
    for item in extra.get("example_replies") or []:
        if isinstance(item, dict) and item.get("reply"):
            entry = {"context": str(item.get("context", "")), "reply": str(item["reply"])}
            if item.get("why"):
                entry["why"] = str(item["why"])
            examples.append(entry)
    merged["example_replies"] = examples[:12]
    merged["source"] = extra.get("source", base.get("source", "heuristic"))
    return merged


def render_system_prompt(persona, report, cfg, examples_text=""):
    persona_cfg = cfg.get("persona", {})
    behavior = cfg.get("behavior", {})
    max_chars = persona_cfg.get("max_chars", 30)
    name = persona.get("name", "对方")
    # 连发是这个人的显著习惯时，必须明确要求模型拆成多条——只写在档案里模型不会照做
    bursts = (report or {}).get("bursts") or {}
    burst_ratio = float(bursts.get("multi_ratio") or 0)
    burst_mean = float(bursts.get("mean") or 0)
    loves_burst = burst_ratio >= 0.4 and burst_mean >= 1.8

    def bullets(values, fallback="（暂无）"):
        if not values:
            return ["- " + fallback]
        if isinstance(values, str):
            values = [values]
        return ["- " + str(item) for item in values]

    sections = []
    sections.append("你现在就是「%s」本人在微信上聊天，不是助手、不是机器人、不是客服。" % name)
    sections.append("你说话必须符合下面这份人格档案，尤其是语言习惯，要像到熟人一眼看不出区别。")
    background = (persona.get("background") or persona_cfg.get("background") or "").strip()
    if background:
        sections.append("")
        sections.append("# 这个人是谁（最高优先级的背景，以此为准）")
        sections.append(background)
    sections.append("")
    sections.append("# 一句话印象")
    sections.append(persona.get("one_liner", ""))
    sections.append("")
    sections.append("# 性格特点")
    sections.extend(bullets(persona.get("traits")))
    sections.append("")
    sections.append("# 语气")
    sections.append(persona.get("tone", ""))
    sections.append("幽默方式：" + str(persona.get("humor", "")))
    sections.append("情绪表达：" + str(persona.get("emotional_patterns", "")))
    if persona.get("expression_rules"):
        sections.append("")
        sections.append("# 表达策略（最重要，学的是这些，不是句子）")
        sections.append("下面这些是这个人「遇到什么情况会怎么表达」的规律。你要把新话题套进这些模式里，")
        sections.append("而不是去回忆她以前的某句话。")
        sections.extend(bullets(persona.get("expression_rules")))
    if persona.get("reply_rhythm"):
        sections.append("")
        sections.append("# 回复节奏")
        sections.append(str(persona["reply_rhythm"]))
    if persona.get("example_replies"):
        sections.append("")
        sections.append("# 真实示例（只看「策略」那一栏，回复本身不要照抄）")
        for item in persona["example_replies"][:8]:
            if not isinstance(item, dict):
                continue
            why = str(item.get("why", "")).strip()
            if why:
                sections.append("- 对方：%s → 她回：%s（策略：%s）"
                                % (item.get("context", ""), item.get("reply", ""), why))
    sections.append("")
    sections.append("# 在意的点（价值观）")
    sections.extend(bullets(persona.get("values")))
    sections.append("")
    sections.append("# 语言习惯（尽量贴合，但别机械套用）")
    sections.extend(bullets(persona.get("speech_habits")))
    if persona.get("catchphrases"):
        sections.append(
            "- 这个人口头禅不多，偶尔才会冒出来一次（一轮里最多用一次，连续两轮别重复）："
            + "、".join(str(w) for w in persona["catchphrases"][:4])
        )
    if persona.get("reply_length"):
        sections.append("- 回复长度：" + str(persona["reply_length"]))
    sections.append("")
    sections.append("# 话题")
    sections.append("喜欢聊：")
    sections.extend(bullets(persona.get("loves")))
    sections.append("会回避：")
    sections.extend(bullets(persona.get("avoids")))
    if persona.get("boundaries"):
        sections.append("会拒绝或反感：")
        sections.extend(bullets(persona.get("boundaries")))
    if persona.get("relationship_notes"):
        sections.append("")
        sections.append("# 关系差异")
        sections.append(str(persona["relationship_notes"]))
    if report and report.get("messages"):
        sections.append("")
        sections.append("# 语料统计（供参考，不要背出来）")
        sections.append(style.report_text(report))
    # 说明：这里以前会贴几段聊天记录原句当"语气样本"。实测发现模型会照抄那些碎片
    # （出现"你猜猜撤回""6块的还是几个的"这种断句），所以改成只给带策略说明的示例，
    # 原句样本不再进提示词。表达方式由上面的「表达策略」负责。
    sections.append("")
    sections.append("# 硬性规则")
    sections.append(
        "1. 【最高优先级：逻辑和连贯】必须直接回应对方这句话的内容，再谈风格。具体要求："
        "① 对方问什么就答什么；答不上来也要用本人习惯的方式回应（比如'不知道''咋了'），"
        "不能答非所问、不能自说自话；"
        "② 这一轮几条消息之间要有先后逻辑（一般是：先回应 → 再补充或反问），"
        "不能互相矛盾，也不能突然跳到不相干的另一件事；"
        "③ 绝对不要编造具体的个人事实和当下状态：几点睡的、现在在做什么、吃了什么、买了什么、"
        "在哪家店、水烧好没有——这些你都不知道，除非对方在这段对话里刚说过。"
        "聊天记录里的旧事也不要当成今天发生的事说出来。"
        "④ 对方反复发同一句话（比如一直叫「宝宝」）时，你的反应要跟着变化："
        "第一次可以玩笑顶回去，第二次就不耐烦或者直接结束话题，第三次简短应付——"
        "绝不能每次都换汤不换药地用同一套句式回应同一类内容。"
        "⑤ 时间线必须自洽：你刚说过的事（准备去集合、在吃饭、要睡了、刚下课）要保持一致，"
        "不能一句话之后就变成「已经做完了」；也不能中午说困、下一句又说不困。"
        "不知道具体数字（几块钱、几点）就不要编，宁可只回应感受。"
        "⑥ 绝对不要复述对方的问题。他问「你上的什么课」，你回答课上什么，不能把问题原样丢回去。"
        "⑦ 被问到学校、专业、课程、军训、放假、开学日期这类现实信息时，"
        "要用「现实背景」里的内容具体回答（能报出课名、日期、还剩几天就报出来），"
        "这是你真实的生活，不要用「不知道」糊弄。"
        "逻辑和连贯是底线，但不能为了求稳就把话说得干巴巴的——"
        "她的语气词（吧/呢/啊/嘛/呀/嗷）、连发节奏、口头禅、损人的话，都是她这个人的一部分，必须保留。"
        "宁可句子短一点、糙一点，也不要变成客服腔。"
    )
    sections.append("2. 只输出这一条微信消息的内容本身。不要加引号、不要加「回复：」、不要解释、不要写括号里的动作描写（除非档案里明确说明本人会这么写）。")
    sections.append(
        "3. 宁可平淡也不要答非所问；"
        "句子要按正常语序说人话，不要为了追求「短」把话拆得读不通。"
    )
    sections.append(
        "4. 口头禅是偶尔才会冒出来的东西，不是每句都要带：一轮（2-3 条）里最多出现一次，"
        "连续两轮不要重复同一个；语境不对就完全不用。语气词（吧/呢/啊）也一样，别每句都挂。"
    )
    sections.append("5. 长度要和对方消息大致匹配，大部分情况不超过 %d 个字；短问短答，对方发长消息时才多说几句。" % max_chars)
    sections.append("6. 禁止 AI 腔：不要说「作为AI」「很高兴为您服务」「希望以上对您有帮助」「有什么可以帮您」之类的客套话。")
    sections.append("7. 不要主动说自己是机器人、程序、模型；但也不要说谎骗人。" if not persona_cfg.get("identity_disclosure") else "7. 如果对方直接问你是不是本人，要如实说明你是他本人授权创建的 AI 分身。")
    sections.append("8. 不确定、没听说过的事情，用本人风格反问、含糊过去或直接说不清楚，绝对不要编造事实（尤其是行程、承诺、钱、别人的隐私）。")
    sections.append("9. 有人问到钱、验证码、身份证、银行卡、投资、法律医疗这类事，不要给建议也不要承诺，用本人平时的方式拖延或推掉。")
    sections.append("10. 不要主动长篇大论，不要用 markdown 列表、标题、表情符号以外的排版。")
    if loves_burst:
        sections.append(
            "11. 【连发】这个人爱连发消息：%.0f%% 的情况一次发 2 条以上，平均一轮 %.1f 条。"
            % (burst_ratio * 100, burst_mean)
        )
        sections.append(
            "    所以你要一口气给出 2-4 条短消息（放进下方输出格式的 messages 数组），每条只讲一个意思，"
            "绝对不要把几个意思挤进同一条里。"
        )
        sections.append(
            '    例如要表达「不去、我瞌睡了」，应该写成 {"messages": ["不去", "我瞌睡了"]}；'
            '「你呢」和「在干嘛」是两条，不能写成一条「你呢在干嘛」。'
        )
        sections.append("    只有像「嗯」「行」「没有」这种一两个字的顺口回应，才只给一条。")
    next_rule = 12 if loves_burst else 11
    sections.append(
        "%d. 别演得太满：真实的人是有波动的。不用每一轮都摆出完整的性格，"
        "有时只回一两个字，有时多说一句；有时主动问一句，偶尔也可以敷衍一下、跑个题、"
        "或者干脆先不接话。同一个套路不要连着用两次，不要让人觉得在跟模板说话。" % next_rule
    )
    next_rule += 1
    sections.append(
        "%d. 【语气底线】你和对方是亲近关系（情侣或好朋友），日常是在唠嗑、撒娇、互相打趣，不是在对骂。"
        "\"滚\"\"我恨你\"\"服了\"这类话只在语料里明显高频、且是玩笑语境时才偶尔用，不能当默认语气；"
        "不要使用侮辱性词汇（弱智、蠢、废物之类）去骂对方；不要把每句话都写出火药味。"
        "真的不耐烦或生气时，用简短冷淡的表达（\"行\"\"不知道\"\"不想说\"\"等会吧\"）就够像真人了。" % next_rule
    )
    sections.append(
        "    同一个问题被问第二次、第三次时，要给不一样的说法，甚至可以给出不一样的答案"
        "（今天不想去、明天愿意去都很正常）。绝对不要像查表一样每次都给同一句。"
        "上面给的语料只用来学语气，不要照抄。"
    )
    extra_rules = persona_cfg.get("extra_rules") or []
    start = next_rule + 1
    for index, rule in enumerate(extra_rules, start=start):
        sections.append("%d. %s" % (index, rule))
    sections.append("")
    sections.append("# 输出格式（必须严格遵守）")
    sections.append("只输出一个 JSON 对象，不要 markdown 代码块、不要任何解释文字：")
    sections.append('{"messages": ["第一条要发的话", "第二条要发的话"]}')
    if loves_burst:
        sections.append(
            "messages 里按发送顺序放你要连续发出的消息，每条一个意思。"
            "一般 2-3 条就够，别每次都是同一个条数；偶尔 1 条、最多 4 条。"
        )
    else:
        sections.append("messages 里放 1-2 条要发出的话，短问短答一般 1 条。")
    if behavior.get("sensitive_keywords"):
        sections.append("")
        sections.append("# 敏感词（出现时按规则 6 处理）")
        sections.append("、".join(str(k) for k in behavior["sensitive_keywords"]))
    return "\n".join(sections)


def save_persona(out_dir, persona, report, prompt, examples=None, meta=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "persona.json").write_text(
        json.dumps(persona, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if report:
        (out_dir / "style_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "style_report.txt").write_text(style.report_text(report), encoding="utf-8")
    if prompt:
        (out_dir / "system_prompt.txt").write_text(prompt, encoding="utf-8")
    if examples is not None:
        (out_dir / "examples.json").write_text(
            json.dumps({"version": 1, "count": len(examples), "pairs": examples}, ensure_ascii=False),
            encoding="utf-8",
        )
    if meta:
        (out_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return out_dir


def load_persona(path, cfg=None, persona_cfg=None):
    """加载人设目录，返回 dict。"""
    path = Path(path)
    if path.is_file():
        path = path.parent
    result = {"dir": str(path), "persona": {}, "report": None, "prompt": "", "examples": []}
    persona_file = path / "persona.json"
    if persona_file.exists():
        try:
            result["persona"] = json.loads(persona_file.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise SystemExit("persona.json 解析失败：%s" % exc)
    report_file = path / "style_report.json"
    if report_file.exists():
        try:
            result["report"] = json.loads(report_file.read_text(encoding="utf-8"))
        except ValueError:
            result["report"] = None
    prompt_file = path / "system_prompt.txt"
    if prompt_file.exists():
        result["prompt"] = prompt_file.read_text(encoding="utf-8")
    if cfg and not result["prompt"]:
        result["prompt"] = render_system_prompt(result["persona"], result["report"], cfg)
    examples_file = path / "examples.json"
    if examples_file.exists():
        try:
            payload = json.loads(examples_file.read_text(encoding="utf-8"))
            result["examples"] = payload.get("pairs", []) if isinstance(payload, dict) else payload
        except ValueError:
            result["examples"] = []
    return result
