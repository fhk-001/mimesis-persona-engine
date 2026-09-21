"""回复编排：把人格档案、记忆、真实语料合成一条"像本人发的"回复。"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from .llm import LLMError, extract_json
from .sanitize import clean
from . import clock, reality

AI_SMELLS = (
    "作为AI",
    "作为一个AI",
    "作为人工智能",
    "我是AI",
    "我是人工智能",
    "我是语言模型",
    "AI助手",
    "很高兴为您",
    "很高兴为你",
    "希望对您有帮助",
    "希望对你有所帮助",
    "有什么可以帮",
    "有什么我可以帮",
    "有什么需要帮",
    "如果您需要",
    "以上信息仅供",
    "感谢您的提问",
)

# 每轮随机给一个"此刻的状态"，让语气像真人一样有波动。
# 注意：只描述情绪和精力，**不要写具体事件或物品**——写了模型就会拿它当谈资，
# 结果答非所问（实测出现过"水还没烧上""我都快渴死了"这种跑题）。
DEFAULT_MOODS = (
    "有点懒",
    "心情还行",
    "有点困",
    "有点烦",
    "精神不错",
    "比较放松",
)

FACT_EXTRACT_PROMPT = """从下面的微信对话里，抽出关于"对方"的、值得长期记住的事实（最多 5 条）。
只输出 JSON：{"facts": ["...", "..."]}。没有就输出 {"facts": []}。
只要客观信息（工作、住址、家人、喜好、约定、重要日期、正在经历的事），不要猜测，不要复述闲聊。"""

SUMMARY_PROMPT = """把下面的微信对话压缩成一段「最近聊过什么」，供后续聊天参考。
要求：
1. 只保留对继续聊天有用的事：正在聊的话题、约定了什么、对方提到的事和情绪状态。
2. 不要逐句复述，不要加评论，不要写"用户""AI"这种称呼，用"对方/我"。
3. 5-8 行，每行一句，短一点。
4. **不要记录"我此刻在做什么/在哪"这类很快过期的状态**（比如"我在上课""我在机房""我在教室"），
   也不要记录任何具体课程名——那种话一旦记错，后面每一轮都会跟着错。只记话题、约定、情绪。
只输出这段概要，不要别的话。"""

# 生成之后的"质检"清单：不针对某句话加规则，而是每次输出都过一遍
REVIEW_PROMPT = """你是这个聊天人格的质检员。下面给你一段微信对话，以及「她」刚才准备发出去的回复。
请逐条检查这条回复：
1. 答非所问：没有回应对方这句话的内容，或者只顾说自己
2. 逻辑断裂：几条消息之间自相矛盾、突然跳到不相干的话题、或者句子读不通
3. 碎片乱码：出现看不懂的词、半截话、像是从别处抄来的片段（例如「你猜猜撤回」这种）
4. 复述问题：把对方的问题原样丢回去当回答
5. 身份/时间不符：把自己说成高中生，或者说的作息、日期、地点和现实背景对不上
6. 机械重复：和她上一轮说的话几乎一样
7. 编造细节：编出对话里没有的具体事件、金额、时间
8. 明明知道却装不知道：背景里已经写明的事实（放假日期、军训结束时间、上课日期），
   却回答「不知道」「看情况」「看放不放」——这算答错
9. 把法定节假日说成不确定：春节、清明、劳动、端午、中秋、国庆、元旦的放假日期是国家统一规定的，
   回答「还没通知」「不一定放」「看放不放」都算答错，必须说确定的日期和天数
10. 时间线错位：军训期间说自己在上课、写作业、见老师（那时还没开课）；或者放假期间说在上课——
    这类把不同阶段的事混在一起的说法，算答错

只要命中任意一条，就重写这条回复：保持同样的条数（2-3 条）、同样的语气和长度风格，
但要把上面这些问题全部修掉。如果完全没问题，就把原话原样返回。

注意：只有真的存在上面这些问题才重写。
风格上的小瑕疵不算问题——短一点、冷淡一点、敷衍一点、有点跳跃，都是这个人的正常说话方式，
不要因为这些就重写。宁可放过，不要误改。

重写的时候**不要改成更正式、更客气、更书面的说法**：一定要保留她的语气词（吧/呢/啊/嘛/呀/嗷）、
连发节奏和口头禅。改完之后应该比原文**更像她**，而不是更不像。如果你重写的版本把语气词写没了，
那这次重写就是失败的。

另外：messages 数组里**每条消息单独放一句**，不要把多条并成一条，
也不要在消息里加「｜」这类分隔符号——那是排版用的，聊天里不会有。

只输出 JSON，不要解释：
{"ok": true 或 false, "messages": ["第一条", "第二条"]}"""

# 用户在聊天里对人格提出修正时，把这句话记下来，之后一直遵守
FEEDBACK_HINTS = (
    "不是这样", "不会这么说", "她不会", "他不会", "应该说", "应该回", "别用", "不要用",
    "你错了", "说错了", "别老是", "以后别", "记住", "语气不对", "太生硬", "她一般",
)


@dataclass
class Outgoing:
    chunks: list = field(default_factory=list)
    delays: list = field(default_factory=list)
    send: bool = False
    reason: str = ""

    def as_text(self):
        return " | ".join(self.chunks)


class Engine:
    def __init__(self, cfg, persona=None, prompt="", retriever=None, llm=None, memory=None, name=""):
        self.cfg = cfg
        self.persona = persona or {}
        self.system_prompt = prompt or ""
        self.retriever = retriever
        self.llm = llm
        self.memory = memory
        self.name = name or self.persona.get("name", "对方")
        self.behavior = cfg.get("behavior", {})
        self.persona_cfg = cfg.get("persona", {})
        self.retrieval_cfg = cfg.get("retrieval", {})
        self.memory_cfg = cfg.get("memory", {})
        self.root = cfg.get("_root") or "."
        self._reply_times = {}
        self._last_reply_at = {}
        self._recent_catchphrases = {}
        self._used_examples = {}
        self._last_examples = []
        self._recent_reply_texts = []
        self.moods = list((cfg.get("persona", {}) or {}).get("moods") or [])
        if not self.moods:
            self.moods = list(DEFAULT_MOODS)
        self.debug = False
        self._fact_counter = {}

    # ---------------- 对外主入口 ----------------
    def plan(self, chat_id, sender, text, is_group=False):
        text = clean(text or "").strip()
        if not text:
            return Outgoing(send=False, reason="空消息")

        history = []
        if self.memory:
            history = self.memory.history_for_prompt(chat_id, self.memory_cfg.get("recent_turns", 12) * 2)
            self.memory.add(chat_id, "user", text, sender=sender)
            self._capture_feedback(chat_id, text)

        allowed, reason = self._gate(chat_id, sender, text, is_group)
        if not allowed:
            return Outgoing(send=False, reason=reason)

        pieces, error = self._generate(chat_id, sender, text, history)
        if not pieces:
            return Outgoing(send=False, reason=error or "模型没有产出内容")

        # 每条消息再按长度兜底切一次，整体最多发 4 条，避免刷屏
        chunks = []
        for piece in pieces:
            chunks.extend(self._chunk(piece))
        chunks = chunks[:4]
        if not chunks:
            return Outgoing(send=False, reason="模型没有产出内容")
        chunks = self._suppress_repeated_catchphrases(chat_id, chunks)
        delays = [self._delay(chunk, index) for index, chunk in enumerate(chunks)]
        if self.memory:
            self.memory.add(chat_id, "assistant", "\n".join(chunks), sender=self.name)
            self._maybe_extract_facts(chat_id, sender, text)
            self._update_summary(chat_id)
        if self._last_examples:
            recent = self._used_examples.get(chat_id, [])
            self._used_examples[chat_id] = (recent + self._last_examples)[-8:]
            self._last_examples = []
        self._reply_times.setdefault(chat_id, []).append(time.time())
        self._last_reply_at[chat_id] = time.time()
        return Outgoing(chunks=chunks, delays=delays, send=True, reason="ok")

    # ---------------- 是否该回复 ----------------
    def _gate(self, chat_id, sender, text, is_group):
        behavior = self.behavior
        if sender in (behavior.get("blacklist") or []) or chat_id in (behavior.get("blacklist") or []):
            return False, "在黑名单里"
        whitelist = behavior.get("whitelist") or []
        if whitelist and sender not in whitelist and chat_id not in whitelist:
            return False, "不在白名单里"
        if is_group:
            if not behavior.get("groups_enabled"):
                return False, "群聊未开启"
            if behavior.get("groups_require_at") and ("@" not in text):
                return False, "群里没 @ 我"

        active_hours = behavior.get("active_hours") or []
        if isinstance(active_hours, (list, tuple)) and len(active_hours) == 2:
            start, end = active_hours
            hour = time.localtime().tm_hour
            if start <= end:
                in_window = start <= hour < end
            else:
                in_window = hour >= start or hour < end
            if not in_window:
                return False, "不在活跃时段(%d-%d点)" % (start, end)

        for keyword in behavior.get("sensitive_keywords") or []:
            if keyword and keyword in text:
                if behavior.get("sensitive_action", "handoff") == "handoff":
                    return False, "命中敏感词「%s」，转人工处理" % keyword

        cooldown = float(behavior.get("cooldown_seconds", 0) or 0)
        last = self._last_reply_at.get(chat_id)
        if last and cooldown and (time.time() - last) < cooldown:
            return False, "冷却中"

        window = [t for t in self._reply_times.get(chat_id, []) if time.time() - t < 60]
        self._reply_times[chat_id] = window
        limit = int(behavior.get("max_replies_per_minute", 0) or 0)
        if limit and len(window) >= limit:
            return False, "每分钟回复上限(%d)已用完" % limit

        probability = float(behavior.get("ignore_probability", 0) or 0)
        if probability > 0 and random.random() < probability:
            return False, "这次故意没回（更像真人）"
        return True, "ok"

    # ---------------- 生成 ----------------
    def _build_messages(self, chat_id, sender, text, history):
        messages = [{"role": "system", "content": self.system_prompt}]
        # 当前阶段的硬约束（军训期间有没有课、哪些说法不可能）——整轮只算一次
        rules = (
            reality.stage_rules(clock.now(), self.root, major=self.persona_cfg.get("major"))
            if self.persona_cfg.get("time_awareness", True)
            else {"phase": "term", "forbidden": (), "note": "", "in_class": True}
        )
        if self.memory and self.memory_cfg.get("long_term"):
            facts = self.memory.facts(chat_id, 12)
            corrections = [fact for fact in facts if fact.startswith("【修正要求】")]
            normal_facts = [fact for fact in facts if not fact.startswith("【修正要求】")]
            if corrections:
                messages.append(
                    {
                        "role": "system",
                        "content": "【用户对你人设的修正要求，必须遵守】\n"
                        + "\n".join("- " + item.replace("【修正要求】", "") for item in corrections),
                    }
                )
            if normal_facts:
                messages.append(
                    {
                        "role": "system",
                        "content": "已知信息（对方以前提过的，需要时自然用上，不要生硬背诵）：\n"
                        + "\n".join("- " + fact for fact in normal_facts),
                    }
                )
        if self.memory and self.memory_cfg.get("summary", True):
            summary = self.memory.get_state("summary:" + chat_id, "")
            summary_text = ""
            if summary:
                summary_text = str(summary)
                # 摘要里如果混进了和当前阶段矛盾的说法（经典案例：军训期间记了
                # "我说在机房上计算机导论"），模型会当成既成事实照着说下去。
                # 所以在这里先按阶段做一次硬筛，矛盾的行直接丢掉。
                dropped = self._filter_summary(summary_text, rules)
                if dropped:
                    print("[记忆] 摘要里有 %d 行和当前阶段矛盾，已忽略：%s" % (len(dropped), " / ".join(dropped)))
                summary_text = "\n".join(
                    line
                    for line in summary_text.splitlines()
                    if line.strip() and line.strip() not in dropped
                ).strip()
            if summary_text:
                messages.append(
                    {
                        "role": "system",
                        "content": "【你们最近聊过什么（背景，用来接话和回忆，不要复述给对方听）】\n"
                        + summary_text
                        + "\n（注意：这段回忆是模糊的，如果和下面的【现实背景】冲突——"
                        "比如回忆里说你正在上课——一律以【现实背景】为准，那是记错了，不要再提。）",
                    }
                )
        if self.retriever and self.retrieval_cfg.get("enabled", True):
            used = set(self._used_examples.get(chat_id, []))
            excluded = {
                index for index, pair in enumerate(self.retriever.pairs)
                if str(pair.get("reply", "")).strip() in used
            }
            hits = self.retriever.search(
                text,
                self.retrieval_cfg.get("top_k", 4),
                exclude=excluded,
            )
            examples = self.retriever.format_examples(hits, self.retrieval_cfg.get("top_k", 4))
            if examples:
                messages.append(
                    {
                        "role": "system",
                        "content": "下面是你以前遇到类似话题时的真实对话。**只学语气和节奏，不要照抄句子**；"
                        "同一个问题被问第二次时，一定要换一种说法：\n"
                        + examples,
                    }
                )
            self._last_examples = [
                str(hit.get("reply", "")).strip()
                for hit in hits
                if str(hit.get("reply", "")).strip()
            ]
        recent_catchphrases = self._recent_catchphrases.get(chat_id, [])
        if recent_catchphrases:
            messages.append(
                {
                    "role": "system",
                    "content": "你最近几轮已经用过这些口头禅了："
                    + "、".join(recent_catchphrases)
                    + "。这一轮一个都不要再用，换成正常把话说清楚。这一轮最多只出现一个口头禅。",
                }
            )
        if self.memory:
            recent_replies = [
                str(row.get("content", "")).replace("\n", "、")
                for row in self.memory.recent(chat_id, 8)
                if row.get("role") == "assistant"
            ][-2:]
            self._recent_reply_texts = recent_replies
            if recent_replies:
                messages.append(
                    {
                        "role": "system",
                        "content": "你最近已经这样回过话：「"
                        + "」「".join(recent_replies)
                        + "」。这一轮不要再说一模一样的话，换一种说法和角度。"
                        "如果对方又发了同样的内容，你的反应也要换——可以换个角度、可以反问他、"
                        "可以开始敷衍、也可以直接结束话题，但不要再用同一套句式和同一类回应。",
                    }
                )
        if self.moods:
            messages.append(
                {
                    "role": "system",
                    "content": "【此刻的状态】%s。" % random.choice(self.moods)
                    + "它只影响你说话的语气和长短。"
                    "绝对不要提到任何具体事件、物品、地点，也不要拿它当聊天内容。",
                }
            )
        if self.persona_cfg.get("time_awareness", True):
            now = clock.now()
            hint = clock.time_hint(
                now,
                night_owl=bool(self.persona_cfg.get("night_owl")),
                phase=rules.get("phase", "term"),
            )
            messages.append(
                {
                    "role": "system",
                    "content": "【现实时间】%s\n%s" % (hint["text"], hint["rule"]),
                }
            )
            if rules.get("note") and rules.get("forbidden"):
                # 这是"绝对不可能发生"的说法清单。放在现实背景**前面**，
                # 并且用最直白的句子说，模型才不会从旧记忆里往回抄。
                messages.append(
                    {
                        "role": "system",
                        "content": "【绝对不可能的说法（违反就是答错）】%s\n"
                        "所以你现在不可能：在上课、在教室、在机房、见老师、上任何一门课。"
                        "被问到上课、作业、老师、课表，只能说还没开始/还没上。"
                        % rules["note"],
                    }
                )
            stage_override = (self.persona_cfg.get("stage") or "").strip()
            if stage_override:
                messages.append({"role": "system", "content": "【你现在的处境】%s" % stage_override})
            else:
                messages.append(
                    {
                        "role": "system",
                        "content": "【现实背景：你的学校、阶段、假期（这是事实，聊天时自然带出来，不要生硬宣布）】\n"
                        + reality.context_text(
                            now,
                            self.root,
                            school=self.persona_cfg.get("school"),
                            major=self.persona_cfg.get("major"),
                            grade=self.persona_cfg.get("grade"),
                            city=self.persona_cfg.get("city"),
                            note=self.persona_cfg.get("schedule_note"),
                            courses=self.persona_cfg.get("courses"),
                        ),
                    }
                )
        for row in history:
            content = str(row.get("content", ""))
            # 历史里她自己说过的错话（"我在机房上计算机导论"）绝不能带进上下文，
            # 否则模型会当成既成事实继续往下说——这是上一轮出问题的真正原因。
            if row.get("role") == "assistant" and self._claim_hits(content, rules):
                print(
                    "[记忆] 丢掉一条和当前阶段矛盾的历史：%s"
                    % content.replace("\n", "，")[:40]
                )
                continue
            messages.append({"role": row["role"], "content": content})
        # 关键事实放在最后（紧挨着对方的消息）：放在前面会被几千字的档案稀释
        if self.persona_cfg.get("time_awareness", True):
            facts = reality.quick_facts(
                clock.now(), self.root, major=self.persona_cfg.get("major")
            )
            if facts:
                messages.append({"role": "system", "content": facts})
        messages.append({"role": "user", "content": text})
        return messages

    def _generate(self, chat_id, sender, text, history):
        """返回 (这次要连续发出的消息列表, 失败原因)。

        模型按提示词要求返回 {"messages": ["...", "..."]}，这样"连发"才真的会分成多条。
        两条经验（实测得出）：
          1. DeepSeek 的 JSON 模式要求对话里出现"json"字样。历史里一旦有 assistant 消息，
             只靠 system 里的说明会直接返回空内容，所以要在最后一条用户消息上再补一句。
          2. 万一还是拿不到内容，就退回纯文本模式，按换行拆条（模型本来就会换行分条）。
        失败原因是给用户看的，要把真实原因带出去，不能只丢一句"没有产出内容"。
        """
        if not self.llm:
            return [], "没有可用的大模型（config.json 里的 api_key 没配？）"
        messages = self._build_messages(chat_id, sender, text, history)
        if self.debug:
            print("\n----- 送给模型的完整上下文 -----")
            for message in messages:
                print("[%s] %s" % (message["role"], message["content"][:1200]))
            print("----- 上下文结束 -----\n")

        json_messages = self._with_json_hint(messages)
        raw = ""
        last_error = None
        # 接口偶尔会抽风（超时、限流），多试一次，别让一条消息就这么没了
        for attempt in range(2):
            try:
                raw = self.llm.chat(json_messages, max_tokens=500, json_mode=True)
                break
            except LLMError as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(2.0)
        if not raw and last_error is not None:
            return [], "调用模型失败：%s" % last_error

        pieces = self._parse_messages(raw)
        if not pieces:
            # 退路：不用 JSON 模式，按换行拆条
            try:
                plain = self.llm.chat(messages, max_tokens=500)
            except LLMError as exc:
                return [], "调用模型失败：%s" % exc
            lines = [line.strip() for line in (plain or "").splitlines() if line.strip()]
            if lines:
                pieces = lines
            elif (raw or "").strip():
                fallback = self._sanitize(raw)
                pieces = [fallback] if fallback else []
            if not pieces:
                return [], "模型返回了空内容（多半是账户额度不足或被限流，稍后再试）"
        joined = "\n".join(pieces)
        if self._smells_ai(joined):
            retry_messages = list(json_messages)
            retry_messages.append({"role": "assistant", "content": joined})
            retry_messages.append(
                {
                    "role": "user",
                    "content": "重写：刚才那句太像AI了。只用本人平时说话的口气，别说助手或模型，"
                    "保持同样的条数，按同样的 JSON 格式输出。",
                }
            )
            try:
                retry_raw = self.llm.chat(retry_messages, max_tokens=300, json_mode=True)
            except LLMError:
                retry_raw = ""
            retry_pieces = self._parse_messages(retry_raw)
            if retry_pieces and not self._smells_ai("\n".join(retry_pieces)):
                pieces = retry_pieces
            else:
                pieces = [self._strip_ai_lines(joined)]
        cleaned = []
        for piece in pieces:
            value = self._sanitize(piece)
            if value:
                cleaned.append(value)
        if not cleaned:
            return [], "模型返回的内容清洗后为空：%s" % (joined[:60] or (raw or "")[:60])

        # 质检：让模型按清单审一遍自己的输出（不是针对某句加规则，而是每次都过）
        reviewed = self._review(chat_id, text, history, cleaned)
        if reviewed:
            cleaned = reviewed

        # 现实一致性硬检查：不靠模型自觉，直接按当前阶段的关键词查一遍
        cleaned = self._enforce_reality(messages, cleaned)
        if not cleaned:
            return [], "这条回复和当前阶段（军训/没开课）对不上，已拦下，稍后再试"

        # 重复检测：这轮如果和最近两轮几乎一样，让它换一种反应重写一次
        answer = "\n".join(cleaned)
        if any(self._too_similar(answer, previous) for previous in self._recent_reply_texts):
            retry_messages = list(messages)
            retry_messages.append({"role": "assistant", "content": answer})
            retry_messages.append(
                {
                    "role": "user",
                    "content": "你刚才这句和上一轮说的几乎一样，太机械了。换一种完全不同的反应："
                    "可以换个角度、可以反问他、可以直接结束话题，句式也要不一样，"
                    "不要再用同一套回应。按同样的 json 格式输出。",
                }
            )
            try:
                retry_raw = self.llm.chat(
                    self._with_json_hint(retry_messages), max_tokens=400, json_mode=True
                )
            except LLMError:
                retry_raw = ""
            retry_pieces = self._parse_messages(retry_raw)
            if retry_pieces:
                candidate = "\n".join(retry_pieces)
                if not any(
                    self._too_similar(candidate, previous)
                    for previous in self._recent_reply_texts
                ):
                    cleaned = [value for value in (self._sanitize(item) for item in retry_pieces) if value]
                    print("[逻辑] 这轮回答和上一轮太像，已自动换一种说法")
        return cleaned, ""

    def _review(self, chat_id, incoming, history, candidates):
        """输出质检：命中清单里的任何一条就重写。这是通用的逻辑保障，不依赖具体句子。"""
        if not self.llm or not candidates:
            return None
        lines = []
        if self.memory and self.memory_cfg.get("summary", True):
            summary = self.memory.get_state("summary:" + chat_id, "")
            if summary:
                lines.append("【之前聊过什么】\n" + str(summary))
        recent = self.memory.recent(chat_id, 6) if self.memory else []
        if recent:
            lines.append(
                "【最近几条对话】\n"
                + "\n".join(
                    "%s：%s"
                    % (
                        "她" if row.get("role") == "assistant" else (row.get("sender") or "对方"),
                        str(row.get("content", "")).replace("\n", "，"),
                    )
                    for row in recent
                )
            )
        lines.append("【对方刚说】" + incoming)
        # 注意：这里刻意不用竖线之类的分隔符——模型会学去，然后在消息里也加这种符号
        lines.append(
            "【她准备发的回复】\n"
            + "\n".join("第%d条：%s" % (i + 1, text) for i, text in enumerate(candidates))
        )
        # 把"确定知道的事实"也给质检员，否则它没法判断"装不知道"这一条
        if self.persona_cfg.get("time_awareness", True):
            facts = reality.quick_facts(clock.now(), self.root, major=self.persona_cfg.get("major"))
            if facts:
                lines.append(facts)
            rules = reality.stage_rules(
                clock.now(), self.root, major=self.persona_cfg.get("major")
            )
            if rules.get("note") and rules.get("forbidden"):
                lines.append(
                    "【当前阶段绝对不可能出现的说法，命中就是答错】"
                    + rules["note"]
                    + " 所以「在上课」「在教室」「在机房」「见老师」「上某门课」"
                    "「课表里有课」都是错的；注意：上面【之前聊过什么】里如果提到过这类说法，"
                    "那是记错了，不算数。"
                )
        try:
            raw = self.llm.chat(
                [
                    {"role": "system", "content": REVIEW_PROMPT},
                    {"role": "user", "content": "\n\n".join(lines)},
                ],
                temperature=0.2,
                json_mode=True,
                max_tokens=400,
            )
        except LLMError:
            return None
        data = extract_json(raw)
        if not isinstance(data, dict):
            return None
        ok = data.get("ok")
        messages = data.get("messages")
        if ok is True or not isinstance(messages, list):
            return None
        fixed = [value for value in (self._sanitize(str(item)) for item in messages) if value]
        if not fixed:
            return None
        print("[质检] 发现逻辑问题，已自动重写：%s" % " / ".join(fixed))
        return fixed[:4]

    # --- 现实一致性硬约束 ---------------------------------------------------
    # 提示词和质检都是"请模型注意"，模型有可能不听（实测就出现过：从记忆里
    # 读到"我在机房上计算机导论"就接着往下说）。所以这里再补一层程序化检查：
    # 生成完按当前阶段的关键词查一遍，命中就强制重写，重写还不行就丢弃那一句。
    FALLBACK_LINES = {
        "training": ("在军训呢", "咋了"),
        "preterm": ("军训刚完", "咋了"),
    }

    def _filter_summary(self, summary, rules):
        """摘要里和当前阶段矛盾的行，返回出来（调用方负责丢掉）。"""
        if not (rules.get("forbidden") or rules.get("courses")):
            return []
        dropped = []
        for line in (summary or "").splitlines():
            line = line.strip()
            if line and self._claim_hits(line, rules):
                dropped.append(line)
        return dropped

    @staticmethod
    def _claim_hits(text, rules):
        """一句话里和当前阶段矛盾的说法（普通词 + 被说成"正在上"的课程名）。"""
        hits = reality.find_claims(text, rules.get("forbidden") or ())
        hits += reality.find_course_claims(text, rules.get("courses") or ())
        return hits

    def _enforce_reality(self, messages, cleaned):
        """生成完之后按当前阶段硬查一遍，命中"不可能的说法"就强制重写。"""
        if not cleaned:
            return cleaned
        rules = reality.stage_rules(
            clock.now(), self.root, major=self.persona_cfg.get("major")
        )
        if not (rules.get("forbidden") or rules.get("courses")):
            return cleaned
        hits = sorted(set(self._claim_hits("\n".join(cleaned), rules)))
        if not hits:
            return cleaned
        print("[现实] 这条回复和当前阶段对不上（%s），强制重写" % "、".join(hits))

        note = rules.get("note") or "现在是军训期间，学校还没开课。"
        retry = list(messages)
        retry.append({"role": "assistant", "content": "\n".join(cleaned)})
        retry.append(
            {
                "role": "user",
                "content": (
                    "你刚才那句是错的：%s\n"
                    "你不可能在上课、在教室、在机房、见老师，也不能提任何一门课，"
                    "更不能说课表里有课要上。原因不用解释给对面听。"
                    "按同样的条数、同样的口气重说一遍，只说你真实的状态"
                    "（在军训、累、腿酸、晒黑、食堂、宿舍）。按同样的 json 格式输出。"
                    % note
                ),
            }
        )
        try:
            raw = self.llm.chat(
                self._with_json_hint(retry), max_tokens=400, json_mode=True
            )
        except LLMError:
            raw = ""
        pieces = self._parse_messages(raw)
        fixed = [value for value in (self._sanitize(item) for item in pieces) if value]
        if fixed and not self._claim_hits("\n".join(fixed), rules):
            return fixed[:4]

        kept = [piece for piece in cleaned if not self._claim_hits(piece, rules)]
        if kept:
            print("[现实] 重写仍不合规，已丢掉对不上的那几条")
            return kept[:4]
        print("[现实] 整条都对不上，退回符合当前阶段的兜底回复")
        return list(self.FALLBACK_LINES.get(rules.get("phase"), ("咋了",)))

    @staticmethod
    def _too_similar(first, second, threshold=0.55):
        """两句话是不是高度雷同（按字符二元组算相似度）。用于防止"关键词触发式"的重复回答。"""
        first = (first or "").strip()
        second = (second or "").strip()
        if not first or not second:
            return False
        if len(first) < 4 or len(second) < 4:
            return first == second

        def grams(text):
            return {text[index : index + 2] for index in range(len(text) - 1)}

        left, right = grams(first), grams(second)
        if not left or not right:
            return False
        return len(left & right) / float(len(left | right)) >= threshold

    @staticmethod
    def _with_json_hint(messages):
        """把"按 json 输出"补在最后一条用户消息上。

        DeepSeek 的 JSON 模式要求对话里出现 "json" 字样；当历史里已经有 assistant 消息时，
        只在 system 提示词里说明是不够的，接口会返回空内容。补在最后一条用户消息上才生效。
        """
        if not messages or messages[-1].get("role") != "user":
            return list(messages)
        updated = list(messages[:-1])
        last = dict(messages[-1])
        last["content"] = str(last.get("content", "")) + "\n\n（请按 json 格式输出 messages 数组）"
        updated.append(last)
        return updated

    @staticmethod
    def _parse_messages(raw):
        """从模型返回里取出消息数组。"""
        data = extract_json(raw)
        if not isinstance(data, dict):
            return []
        items = data.get("messages")
        if isinstance(items, str):
            items = [items]
        if not isinstance(items, list):
            return []
        result = []
        for item in items:
            if isinstance(item, str) and item.strip():
                result.append(item.strip())
            if len(result) >= 6:
                break
        return result

    @staticmethod
    def _smells_ai(text):
        return any(smell in text for smell in AI_SMELLS)

    def _suppress_repeated_catchphrases(self, chat_id, chunks):
        """压制口头禅复读。

        模型很容易逮住一个口头禅反复用（比如每轮都甩一句"写作业了"）。
        这里只处理"整条消息就是一个口头禅"的情况：如果它最近几轮已经出现过，
        这一轮就把这条删掉。改动只发生在整条消息上，不会破坏正常句子。
        """
        phrases = [
            str(item).strip()
            for item in (self.persona.get("catchphrases") or [])
            if str(item).strip()
        ]
        if not phrases or len(chunks) <= 1:
            return chunks
        recent = self._recent_catchphrases.get(chat_id, [])
        kept = []
        for chunk in chunks:
            text = chunk.strip().strip("。！？~～ ")
            hit = next((phrase for phrase in phrases if text == phrase), None)
            if hit and hit in recent:
                continue
            kept.append(chunk)
        if not kept:
            kept = chunks[-1:]
        used = [phrase for phrase in phrases if any(phrase in chunk for chunk in kept)]
        self._recent_catchphrases[chat_id] = (recent + used)[-4:]
        return kept

    @staticmethod
    def _strip_ai_lines(text):
        lines = []
        for line in text.splitlines():
            if any(smell in line for smell in AI_SMELLS):
                continue
            lines.append(line)
        cleaned = "\n".join(lines).strip()
        return cleaned or "嗯嗯"

    @staticmethod
    def _sanitize(text):
        text = (text or "").strip()
        # 全角空格（　）和连续空格在微信里会显示成一个大空隙，看起来像排版错误。
        # 本人确实有用空格断句的习惯，所以保留单个半角空格，只把全角/连续空格规整掉。
        text = text.replace("\u3000", " ").replace("\t", " ")
        text = re.sub(r"[ ]{2,}", " ", text)
        text = "\n".join(line.strip() for line in text.splitlines())
        text = re.sub(r"^\s*(?:回复|答复|回答)\s*[:：]\s*", "", text)
        text = re.sub(r"^\s*```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text)
        text = text.replace("**", "").replace("##", "").replace("`", "")
        text = re.sub(r'^\s*[“"「『](.{1,200})[”"」』]\s*$', r"\1", text, flags=re.S)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # ---------------- 拆条与节奏 ----------------
    def _chunk(self, text):
        limit = max(6, int(self.persona_cfg.get("max_chars", 30)))
        text = text.strip()
        # 模型有时会把多条消息用竖线连成一条（那是我们内部日志用的分隔符，不该出现在消息里）。
        # 遇到就当成换行处理，拆成多条发出去。
        text = re.sub(r"\s*[｜|︱]\s*", "\n", text)
        if not text:
            return []
        explicit = [part.strip() for part in text.splitlines() if part.strip()]
        if len(explicit) > 1:
            parts = []
            for line in explicit:
                parts.extend(self._split_sentences(line, limit))
        else:
            parts = self._split_sentences(text, limit)
        if not self.persona_cfg.get("split_long_replies", True):
            merged = "".join(parts)
            return [merged] if merged else []
        chunks, buffer = [], ""
        for part in parts:
            if not buffer:
                buffer = part
            elif len(buffer) + len(part) <= limit:
                buffer += part
            else:
                chunks.append(buffer)
                buffer = part
        if buffer:
            chunks.append(buffer)
        result = []
        for chunk in chunks:
            while len(chunk) > limit * 2:
                result.append(chunk[:limit])
                chunk = chunk[limit:]
            if chunk:
                result.append(chunk)
        return result[:4] or [text[: limit * 2]]

    @staticmethod
    def _split_sentences(text, limit):
        parts = re.split(r"(?<=[。！？!?~～…\n])", text)
        output = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if len(part) <= limit * 2:
                output.append(part)
                continue
            pieces = [p for p in re.split(r"(?<=[，,；;])", part) if p.strip()]
            output.extend(piece.strip() for piece in pieces)
        return output

    def _delay(self, chunk, index=0):
        """算出这条消息该等多久再发出去。

        真人的节奏不是"每条都等一样久"：
          第一条是"看到消息 -> 想一下 -> 打完字"，所以要慢一点；
          同一轮后面几条是接着打的，间隔短得多。
        """
        behavior = self.behavior
        minimum = float(behavior.get("min_delay", 1.0))
        maximum = float(behavior.get("max_delay", 6.0))
        speed = max(1.0, float(behavior.get("typing_chars_per_second", 6.0)))
        typing = len(chunk) / speed
        if index == 0:
            # 看到消息后先"想一下"，再打字；长消息自然更久
            delay = minimum + typing + random.uniform(0.0, 1.2)
            delay = min(delay, maximum)
        else:
            # 连发的后续几条：像手一直在键盘上，间隔短、波动小
            delay = 0.35 + typing * 0.7 + random.uniform(0.0, 0.5)
            delay = min(delay, 2.0)
        return round(max(0.35, delay), 2)

    # ---------------- 长期记忆 ----------------
    def _capture_feedback(self, chat_id, text):
        """用户在聊天里纠正人格时，把这句话记成"修正要求"，之后一直遵守。"""
        if not (self.memory and self.memory_cfg.get("learn_feedback", True)):
            return
        if not any(hint in text for hint in FEEDBACK_HINTS):
            return
        content = text[:160]
        if self.memory.add_fact(chat_id, "【修正要求】" + content):
            print("[记忆] 记下一条修正要求：%s" % content[:40])

    def _update_summary(self, chat_id):
        """滚动摘要：把最近聊过的内容压成一段背景，让它后面能"瞻前顾后"。"""
        if not (self.memory and self.memory_cfg.get("summary", True) and self.llm):
            return
        every = int(self.memory_cfg.get("summary_every", 12) or 12)
        last = int(self.memory.get_state("summary_id:" + chat_id, 0) or 0)
        pending = self.memory.messages_since(chat_id, last, 60)
        if len(pending) < every:
            return
        old = self.memory.get_state("summary:" + chat_id, "") or ""
        transcript = "\n".join(
            "%s：%s"
            % (
                "我" if row["role"] == "assistant" else (row.get("sender") or "对方"),
                str(row["content"]).replace("\n", " "),
            )
            for row in pending
        )
        prompt = SUMMARY_PROMPT + "\n"
        if old:
            prompt += "已有的概要（请把新内容合并进去）：\n%s\n\n" % old
        prompt += "新对话：\n" + transcript
        try:
            summary = self.llm.chat(
                [{"role": "user", "content": prompt}], temperature=0.2, max_tokens=450
            )
        except LLMError:
            return
        summary = (summary or "").strip()
        if not summary:
            return
        # 存之前先按当前阶段筛一遍：摘要里一旦写进"我在机房上计算机导论"，
        # 后面每一轮都会被当成既成事实，这是自我强化的记忆污染。
        rules = reality.stage_rules(
            clock.now(), self.root, major=self.persona_cfg.get("major")
        )
        dropped = set(self._filter_summary(summary, rules))
        if dropped:
            summary = "\n".join(
                line
                for line in summary.splitlines()
                if line.strip() and line.strip() not in dropped
            ).strip()
            print("[记忆] 摘要里 %d 行和当前阶段矛盾，已丢弃" % len(dropped))
        if not summary:
            return
        self.memory.set_state("summary:" + chat_id, summary[:900])
        self.memory.set_state("summary_id:" + chat_id, pending[-1]["id"])
        print("[记忆] 已更新对话摘要（覆盖 %d 条消息）" % len(pending))

    def _maybe_extract_facts(self, chat_id, sender, text):
        if not (self.memory and self.memory_cfg.get("long_term") and self.llm):
            return
        if getattr(self.llm, "is_mock", False):
            return
        counter = self._fact_counter.get(chat_id, 0) + 1
        self._fact_counter[chat_id] = counter
        if counter % 10 != 0:
            return
        recent = self.memory.recent(chat_id, 12)
        if not recent:
            return
        transcript = "\n".join(
            "%s: %s" % ("本人" if row["role"] == "assistant" else (row.get("sender") or "对方"), row["content"])
            for row in recent
        )
        try:
            raw = self.llm.chat(
                [
                    {"role": "system", "content": FACT_EXTRACT_PROMPT},
                    {"role": "user", "content": transcript},
                ],
                temperature=0.2,
                json_mode=True,
                max_tokens=300,
            )
        except LLMError:
            return
        from .llm import extract_json

        data = extract_json(raw)
        if not isinstance(data, dict):
            return
        for fact in data.get("facts") or []:
            if isinstance(fact, str):
                self.memory.add_fact(chat_id, fact)
