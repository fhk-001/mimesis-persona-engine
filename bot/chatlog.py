"""聊天记录解析：把各种导出格式统一成 (说话人, 时间, 内容) 记录。

支持：
    1. 微信 PC 端导出 txt（"昵称 时间" 行 + 正文行 的常见导出工具格式）
    2. 单行格式："昵称 2024-03-02 21:14:07 你好" 或 "[时间] 昵称: 你好"
    3. "昵称: 内容" 这种最朴素的格式
    4. CSV / TSV（列名自动识别 时间/发送者/内容）
    5. JSON / JSONL（字典列表，字段名自动识别）
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MEDIA_RE = re.compile(
    r"^\[(图片|表情|动画表情|视频|语音|文件|位置|链接|转账|红包|聊天记录|小程序|音乐|卡券|名片|视频号|引用|合辑|拼图|斗图|GIF)\]$"
)
SYSTEM_HINTS = (
    "撤回了一条消息",
    "撤回了一条",
    "撤回了什么",
    "拍了拍",
    "邀请",
    "加入了群聊",
    "移出了群聊",
    "你已添加",
    "以上是打招呼",
    "领取了你的红包",
    "开启了朋友验证",
    "消息已发出",
)

DATE_PAT = r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)"
TIME_PAT = r"(\d{1,2}:\d{2}(?::\d{2})?)"

HEADER_PATTERNS = (
    re.compile(r"^(?P<sender>[^:：]{1,24}?)\s+" + DATE_PAT + r"\s+" + TIME_PAT + r"\s*$"),
    re.compile(r"^" + DATE_PAT + r"\s+" + TIME_PAT + r"\s+(?P<sender>[^:：\s]{1,24})\s*$"),
    re.compile(
        r"^\[" + DATE_PAT + r"\s+" + TIME_PAT + r"\]\s*(?P<sender>[^:：]{1,24})[:：]\s*(?P<text>.*)$"
    ),
    re.compile(
        r"^" + DATE_PAT + r"\s+" + TIME_PAT + r"\s+(?P<sender>[^:：]{1,24})[:：]\s*(?P<text>.*)$"
    ),
)
INLINE_PATTERN = re.compile(r"^(?P<sender>[^:：]{1,24})[:：]\s*(?P<text>.+)$")

SENDER_KEYS = ("sender", "from", "nickname", "talker", "screen_name", "speaker", "who", "name", "发送者", "昵称", "用户", "好友", "发件人")
TEXT_KEYS = ("content", "text", "message", "msg", "body", "内容", "消息", "正文")
TIME_KEYS = ("time", "date", "timestamp", "datetime", "created_at", "时间", "日期")


@dataclass
class Record:
    sender: str
    text: str
    ts: object = None
    kind: str = "text"

    def as_dict(self):
        return {
            "sender": self.sender,
            "text": self.text,
            "ts": self.ts.isoformat(sep=" ") if isinstance(self.ts, datetime) else None,
            "kind": self.kind,
        }


@dataclass
class ParsedLog:
    records: list = field(default_factory=list)
    source: str = ""

    @property
    def speakers(self):
        return Counter(r.sender for r in self.records if r.kind == "text")


def _parse_ts(date_s, time_s):
    if not date_s or not time_s:
        return None
    date_s = date_s.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-")
    date_s = "-".join(p for p in date_s.split("-") if p)
    hh, mm, ss = 0, 0, 0
    parts = time_s.split(":")
    try:
        hh = int(parts[0])
        mm = int(parts[1]) if len(parts) > 1 else 0
        ss = int(parts[2]) if len(parts) > 2 else 0
        if "-" in date_s:
            y, m, d = date_s.split("-")[:3]
            return datetime(int(y), int(m), int(d), hh, mm, ss)
    except (ValueError, IndexError):
        return None
    return None


def _classify(text, sender):
    stripped = (text or "").strip()
    if not stripped:
        return "empty"
    # OCR 经常在字之间插空格（"你撤回 了一条消息"），比较前先把空格去掉，
    # 否则系统提示会被当成正常聊天内容（实测因此把"你猜猜撤回"喂成了口头禅）
    compact = re.sub(r"\s+", "", stripped)
    if any(hint in compact for hint in SYSTEM_HINTS):
        return "system"
    if MEDIA_RE.match(stripped):
        return "media"
    return "text"


def _match_header(line):
    """返回 (sender, ts, inline_text) 或 None。"""
    for pattern in HEADER_PATTERNS:
        m = pattern.match(line)
        if not m:
            continue
        groups = m.groupdict()
        sender = (groups.get("sender") or "").strip()
        if not sender:
            continue
        ts = _parse_ts(groups.get("date"), groups.get("time"))
        return sender, ts, groups.get("text")
    return None


def parse_txt(text):
    records = []
    current = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        matched = _match_header(line)
        if matched:
            sender, ts, inline = matched
            current = Record(sender=sender, text=(inline or "").strip(), ts=ts)
            records.append(current)
            continue
        if current is None:
            m = INLINE_PATTERN.match(line)
            if m:
                records.append(Record(sender=m.group("sender").strip(), text=m.group("text").strip()))
            continue
        if current.text:
            current.text = current.text + "\n" + line
        else:
            current.text = line
    for record in records:
        record.kind = _classify(record.text, record.sender)
    return [r for r in records if r.kind != "empty"]


def _pick(row, keys):
    lowered = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, ""):
            return str(value)
    return ""


def parse_rows(rows):
    records = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sender = _pick(row, SENDER_KEYS)
        text = _pick(row, TEXT_KEYS)
        ts_raw = _pick(row, TIME_KEYS)
        ts = None
        if ts_raw:
            ts = _parse_ts(ts_raw[:10], ts_raw[11:19]) or _parse_ts(ts_raw[:10], "")
        if not sender or not text:
            continue
        if sender in ("我", "me", "Me", "myself"):
            sender = "我"
        record = Record(sender=sender, text=text.strip(), ts=ts)
        record.kind = _classify(record.text, sender)
        records.append(record)
    return [r for r in records if r.kind != "empty"]


def parse_text(text, suffix=""):
    suffix = (suffix or "").lower()
    if suffix in (".json",):
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("messages", "records", "data", "items", "list"):
                if isinstance(data.get(key), list):
                    return parse_rows(data[key])
            return parse_rows([data])
        if isinstance(data, list):
            return parse_rows(data)
        return []
    if suffix in (".jsonl", ".ndjson"):
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return parse_rows(rows)
    return parse_txt(text)


def load_chat_log(path):
    path = Path(path)
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")

    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv"):
        delimiter = "\t" if suffix == ".tsv" else ","
        try:
            dialect = csv.Sniffer().sniff(text[:4000], delimiters=",\t;")
            delimiter = dialect.delimiter
        except csv.Error:
            pass
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        rows = [row for row in reader]
        return ParsedLog(records=parse_rows(rows), source=str(path))
    if suffix in (".txt", ".log", ".md", ""):
        records = parse_txt(text)
        if records:
            return ParsedLog(records=records, source=str(path))
    return ParsedLog(records=parse_text(text, suffix), source=str(path))


def build_pairs(records, target, context_window=3):
    """把聊天记录变成 (对方说的话 -> 目标人物回复) 的语料对。"""
    pairs = []
    for index, record in enumerate(records):
        if record.sender != target or record.kind != "text" or not record.text.strip():
            continue
        context = []
        cursor = index - 1
        while cursor >= 0 and len(context) < context_window:
            previous = records[cursor]
            if previous.sender == target:
                break
            if previous.kind == "text":
                context.insert(0, {"sender": previous.sender, "text": previous.text})
            cursor -= 1
        if not context:
            continue
        pairs.append({"context": context, "reply": record.text.strip()})
    return pairs


def to_jsonl(records, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record.as_dict(), ensure_ascii=False) + "\n")
    return path
