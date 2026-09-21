"""现实背景：让她活在「现在」。

为什么需要：语料是高三时期的聊天记录，但人已经是大一新生了。
只按语料说话，她会永远停在高三（聊模考、晚自习、班主任），
和「今天几号、在哪个学校、什么阶段」对不上。

数据来源：
  1. 学校教务处发布的学年校历（放在 data/school_calendar.json，不入库）
  2. 国务院办公厅节假日安排（data/holidays.json，由公共 API 写入）
  3. 大学生活的通行规律（军训、上课、期末、寒暑假等）

为什么校历要单独放文件：**校名 + 年级 + 校历日期加起来足以定位到一个具体的人**，
所以真实数据不进代码库，代码里只留一套示例值。没有那个文件时程序照常能跑，
只是阶段判断按示例校历走。
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

WEEKDAYS = "一二三四五六日"

# 国务院统一安排的法定节假日：日期是确定的，不需要"等通知"
MAJOR_HOLIDAYS = ("元旦", "春节", "清明节", "劳动节", "端午节", "中秋节", "国庆节")

# ↓↓↓ 以下都是**示例值**，真实校历放 data/school_calendar.json（已被 .gitignore 排除）
DEFAULT_SCHOOL = {
    "name": "示例大学",
    "campus": "示例校区",
    "city": "示例市",
}

DEFAULT_TERMS = (
    {
        "name": "2026-2027 学年第一学期",
        "register": ("2026-09-05", "2026-09-06"),
        "military_training": ("2026-09-07", "2026-09-21"),
        "class_start": "2026-09-22",
        "class_start_others": "2026-09-07",
        "weeks": 19,
        "winter_break": "2027-01-18",
    },
    {
        "name": "2026-2027 学年第二学期",
        "register": ("2027-02-20", "2027-02-21"),
        "class_start": "2027-02-22",
        "sports_meet": ("2027-04-15", "2027-04-16"),
        "summer_break": "2027-07-05",
        "summer_term": ("2027-07-05", "2027-08-01"),
    },
)

# 大一课表（按专业给不同的默认值）——同样是示例值
DEFAULT_MAJOR_COURSES = {
    "数据": (
        "高等数学",
        "线性代数",
        "C 语言程序设计",
        "大学英语",
        "计算机导论",
        "数据科学导论",
        "思想道德与法治",
        "体育",
    ),
    "软件": (
        "高等数学",
        "线性代数",
        "C 语言程序设计",
        "大学英语",
        "计算机导论",
        "离散数学",
        "思想道德与法治",
        "体育",
    ),
}
DEFAULT_COURSES_FALLBACK = (
    "高等数学",
    "大学英语",
    "计算机导论",
    "思想道德与法治",
    "体育",
)


def _load_local_calendar():
    """真实校历放在 data/school_calendar.json；这个文件不进代码库。"""
    path = Path(__file__).resolve().parent.parent / "data" / "school_calendar.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


_local_calendar = _load_local_calendar()

SCHOOL = {**DEFAULT_SCHOOL, **(_local_calendar.get("school") or {})}
TERMS = tuple(_local_calendar.get("terms") or DEFAULT_TERMS)
MAJOR_COURSES = _local_calendar.get("major_courses") or DEFAULT_MAJOR_COURSES
DEFAULT_COURSES = tuple(_local_calendar.get("default_courses") or DEFAULT_COURSES_FALLBACK)


def courses_for(major, override=None):
    if override:
        return tuple(override)
    for key, value in MAJOR_COURSES.items():
        if key in (major or ""):
            return value
    return DEFAULT_COURSES


def course_line(day, term, major, override=None):
    """按当前时间说清楚"到底有没有课在上"——军训期间是不能说自己在上课的。"""
    names = "、".join(courses_for(major, override))
    if not term:
        return "现在是假期，没有课，也不在学校上课。"

    training = term.get("military_training")
    if training:
        start, end = _date(training[0]), _date(training[1])
        if start <= day <= end:
            return (
                "你还在军训，**一节正式课都没开始上**（这学期的课表是：%s，但要到 %d 月 %d 日才开课）。"
                "被问到上课、作业、老师，都要说还没开始，最多提「课表已经发了」。"
                % (names, _date(term["class_start"]).month, _date(term["class_start"]).day)
            )
        if day < _date(term.get("class_start") or "2100-01-01"):
            return (
                "军训刚结束，还没开课（%d 月 %d 日开始上课）。这学期要上的课：%s。"
                % (_date(term["class_start"]).month, _date(term["class_start"]).day, names)
            )
    class_start = term.get("class_start")
    if class_start and day >= _date(class_start):
        return "这学期在上的课：%s。" % names
    return "这学期的课表：%s。" % names

SCHEDULE_TEXT = (
    "上午 8:00 开始上课（一到四节），下午 14:00 开始（五到八节），"
    "晚上有晚自习或社团活动；军训期间早上六点左右出操"
)

# ---------------------------------------------------------------------------
# 阶段硬约束
#
# 为什么要有这段：光在提示词里写"军训期间别说自己在上课"是不够的。
# 实测出现过：模型从历史/摘要里读到自己之前说过的"在机房上计算机导论"，
# 就当成既成事实继续往下说。提示词挡不住自己说过的话，所以这里做一层
# **程序化**的检查——生成完之后按关键词查一遍，命中就强制重写。
# ---------------------------------------------------------------------------

# 军训/放假期间不可能出现的"我正在上课"类说法
CLASS_CLAIM_WORDS = (
    "上课",
    "下课",
    "机房",
    "教室",
    "讲台",
    "PPT",
    "讲课",
    "还得上",
    "有课",
)

# 出现这些字，说明这句话是"还没开始/已经结束/在讨论而不是在经历"，
# 就不算矛盾。例："还没开始上课"、"放假不用上课"、"要到 9 月 28 日才上课"。
# 注意不能用光秃秃的"不"——"不睡\n上课呢"这种断句会被误判成否定。
NEGATION_MARKS = ("没", "未", "别", "才", "以后", "开学后", "等", "将", "不用", "不会")


def _tokens(text, words, window=5):
    """在 text 里找 words；如果命中位置的**前面 5 个字**里有否定/将来词，就不算命中。

    只看前面，是因为中文的否定和状语都在前面（"还没开始上课"）。
    """
    text = text or ""
    hits = []
    for word in words:
        start = 0
        while True:
            index = text.find(word, start)
            if index < 0:
                break
            start = index + len(word)
            prefix = text[max(0, index - window):index]
            if any(mark in prefix for mark in NEGATION_MARKS):
                continue
            hits.append(word)
    return hits


def find_claims(text, words=None):
    """找出这句话里"和当前阶段冲突"的说法（用于生成后的程序化检查）。"""
    return _tokens(text, words or CLASS_CLAIM_WORDS)


# 课程名单独处理：说"课表上有计算机导论"是正常的，说"我在上计算机导论"才是错的。
# 所以课程名只有在**前面出现"上/在/刚/学/去"**这类动词时才算矛盾。
CLAIM_VERBS = ("上", "在", "刚", "学", "去", "来", "正在")


def find_course_claims(text, courses, window=6):
    if not courses:
        return []
    text = text or ""
    hits = []
    for course in courses:
        start = 0
        while True:
            index = text.find(course, start)
            if index < 0:
                break
            start = index + len(course)
            prefix = text[max(0, index - window):index]
            if any(mark in prefix for mark in NEGATION_MARKS):
                continue
            if not any(verb in prefix for verb in CLAIM_VERBS):
                continue
            hits.append(course)
    return hits


def phase_of(now):
    """此刻的处境类型：training（军训）/ preterm（军训完还没开课）/ term（在上课）/ break（假期）。"""
    day = now.date() if isinstance(now, datetime) else now
    term = _term_of(day)
    if not term:
        return "break"
    training = term.get("military_training")
    if training and _date(training[0]) <= day <= _date(training[1]):
        return "training"
    class_start = term.get("class_start")
    if class_start and day < _date(class_start):
        return "preterm"
    return "term"


def stage_rules(now, root, school=None, major=None, grade=None, city=None, note=None, courses=None):
    """当前阶段"允许说什么、绝对不可能说什么"。

    返回的 forbidden 是关键词列表：生成完之后按它查一遍，命中就重写。
    """
    day = now.date() if isinstance(now, datetime) else now
    term = _term_of(day)
    phase = phase_of(day)
    holidays = load_holidays(root)
    item = holidays.get(day.strftime("%Y-%m-%d"))
    on_holiday = bool(item and item.get("is_holiday"))

    not_yet_in_class = phase in ("training", "preterm")
    rules = {
        "phase": phase,
        "on_holiday": on_holiday,
        "in_class": (phase == "term") and not on_holiday,
        "forbidden": CLASS_CLAIM_WORDS if not_yet_in_class else (),
        # 课程名单独给出来：说"课表上排着计算机导论"没事，说"我在上计算机导论"才是错的
        "courses": tuple(courses_for(major, courses)) if not_yet_in_class else (),
        "note": "",
    }

    if phase == "training" and term and term.get("military_training"):
        start, end = _date(term["military_training"][0]), _date(term["military_training"][1])
        class_start = _date(term["class_start"])
        rules["note"] = (
            "现在是军训期间（%d 月 %d 日 到 %d 月 %d 日），学校 %d 月 %d 日才开课。"
            "你每天在训练、站军姿，还没有上过一节课，也没有老师给你上过课。"
            % (start.month, start.day, end.month, end.day, class_start.month, class_start.day)
        )
    elif phase == "preterm":
        class_start = _date(term["class_start"])
        rules["note"] = (
            "军训已经结束，%d 月 %d 日才正式开始上课，现在还没上过课。"
            % (class_start.month, class_start.day)
        )
    elif on_holiday:
        rules["note"] = "今天是%s，放假在家/在外玩，没有课。" % item.get("name", "假期")
    return rules


def _date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


def load_holidays(root):
    path = Path(root) / "data" / "holidays.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def refresh_holidays(root, years=None, timeout=25):
    """从公共接口拉取国务院节假日安排，更新到 data/holidays.json。

    每年 11 月国务院会公布下一年的安排，那时候跑一次 `python run.py refresh`
    就能把新一年的假期、调休补班同步进来。
    """
    today = datetime.now()
    if not years:
        years = [today.year, today.year + 1]
    data = load_holidays(root)
    added = 0
    for year in years:
        url = "https://timor.tech/api/holiday/year/%d" % year
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 - 拉不到就跳过，不影响已有数据
            continue
        for key, item in (payload.get("holiday") or {}).items():
            full_key = key if len(key) == 10 else "%d-%s" % (year, key)
            entry = {
                "name": item.get("name") or "",
                "is_holiday": bool(item.get("holiday")),
                "target": item.get("target") or "",
            }
            if data.get(full_key) != entry:
                added += 1
            data[full_key] = entry
    path = Path(root) / "data" / "holidays.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return {"file": str(path), "total": len(data), "updated": added, "years": years}


def _term_of(day):
    for term in TERMS:
        start = _date(term.get("class_start_others") or term["class_start"])
        if term.get("register"):
            start = min(start, _date(term["register"][0]))
        end_value = term.get("winter_break") or term.get("summer_break") or "2027-07-04"
        if start <= day <= _date(end_value):
            return term
    return None


def _stage_of(day, term, holidays=None):
    # 法定假期优先：国庆、中秋这种日子，说"刚开学上课"就是答错
    item = (holidays or {}).get(day.strftime("%Y-%m-%d"))
    if item and item.get("is_holiday"):
        name = item.get("name") or "假期"
        return (
            "%s放假中" % name,
            "放假不用上课，在宿舍补觉、回家、出去玩、点外卖都正常；会嫌弃假期过得快",
        )
    if not term:
        return "假期中", "不在学期内，可能是寒暑假，作息比较松散"
    training = term.get("military_training")
    if training:
        start, end = _date(training[0]), _date(training[1])
        if start <= day <= end:
            week = (day - start).days // 7 + 1
            return (
                "军训进行中（第 %d 周）" % week,
                "军训安排：早上六点左右出操，白天站军姿、走方阵、练军体拳、拉歌，"
                "晚上整理内务或写军训心得；会累、腿酸、晒黑，会吐槽教官严、天气热",
            )
    class_start = term.get("class_start")
    if class_start and day < _date(class_start):
        return (
            "军训刚结束，等着上课",
            "军训结束了，这两天在宿舍歇着、收拾东西，准备开始正式上课",
        )
    if class_start and day >= _date(class_start):
        weeks = (day - _date(class_start)).days // 7 + 1
        if weeks <= 6:
            return (
                "刚开学上课（第 %d 周）" % weeks,
                "大一刚开学：课不算多但都很基础（高数、C 语言、英语、计算机导论），"
                "还在适应大学节奏，会聊选课、新同学、宿舍、社团、食堂",
            )
        if weeks <= 13:
            return ("学期中（第 %d 周）" % weeks, "上课、交作业、社团活动，日子比较规律")
        return ("接近期末（第 %d 周）" % weeks, "在复习、赶作业、准备考试，会抱怨熬夜和背书")
    return "学期中", ""


def _next_holiday(day, holidays):
    for offset in range(0, 40):
        candidate = day + timedelta(days=offset)
        item = holidays.get(candidate.strftime("%Y-%m-%d"))
        if item and item.get("is_holiday"):
            return candidate, item.get("name", "假期")
    return None, None


def _holiday_length(start, holidays):
    """这个假期一共连着放几天"""
    days = 0
    cursor = start
    while True:
        item = holidays.get(cursor.strftime("%Y-%m-%d"))
        if not item or not item.get("is_holiday"):
            break
        days += 1
        cursor = cursor + timedelta(days=1)
    return days


def _holiday_window(start, holidays):
    """返回这个假期的 (开始, 结束, 天数)"""
    days = _holiday_length(start, holidays)
    if days <= 0:
        return start, start, 0
    return start, start + timedelta(days=days - 1), days


def _is_major(name):
    return any(key in (name or "") for key in MAJOR_HOLIDAYS)


def unpublished_holiday_note(day, holidays, ahead=45):
    """放假安排只公布到某一天为止，之后的日子国家还没发通知。

    不加这一句的话，模型遇到"2027 年元旦放几天"这种问题只能编——因为质检清单
    要求法定节假日必须报出确定日期。而那份安排要到前一年 11 月才公布。
    """
    if not holidays:
        return ""
    last = max(_date(key) for key in holidays)
    if (last - day).days >= ahead:
        return ""
    return (
        "注意：%d 月 %d 日之后的放假安排国家还没公布（每年 11 月才发下一年度的通知）。"
        "被问到元旦、春节这类还没公布的日子，说「还没公布、等通知」就是对的，"
        "不要编日期；已经公布的节日照旧要直接报日期和天数。" % (last.month, last.day)
    )


def upcoming_major_holidays(day, holidays, count=2, window=90):
    """找出接下来几个法定节假日（带确定日期）。"""
    found = []
    seen = set()
    for offset in range(0, window):
        candidate = day + timedelta(days=offset)
        item = holidays.get(candidate.strftime("%Y-%m-%d"))
        if not item or not item.get("is_holiday"):
            continue
        name = item.get("name", "")
        if not _is_major(name) or name in seen:
            continue
        seen.add(name)
        start, end, days = _holiday_window(candidate, holidays)
        found.append((name, start, end, days, (candidate - day).days))
        if len(found) >= count:
            break
    return found


def _holiday_days_before(day, holidays):
    """今天已经是这个假期的第几天（前面连着放的天数）"""
    days = 0
    cursor = day - timedelta(days=1)
    while days < 15:
        item = holidays.get(cursor.strftime("%Y-%m-%d"))
        if not item or not item.get("is_holiday"):
            break
        days += 1
        cursor = cursor - timedelta(days=1)
    return days


def key_dates(day, term):
    """近期关键日期：军训结束、开学、放假等（写清楚日期，避免模型瞎猜）"""
    lines = []
    if not term:
        return lines
    training = term.get("military_training")
    if training:
        start, end = _date(training[0]), _date(training[1])
        if day <= end:
            lines.append(
                "军训：%d 月 %d 日 到 %d 月 %d 日，%d 号结束"
                % (start.month, start.day, end.month, end.day, end.day)
            )
            if day < end:
                lines.append("距离军训结束还有 %d 天" % (end - day).days)
    class_start = term.get("class_start")
    if class_start:
        target = _date(class_start)
        if day <= target:
            lines.append("正式上课：%d 月 %d 日开始" % (target.month, target.day))
            if day < target:
                lines.append("距离开始上课还有 %d 天" % (target - day).days)
    winter = term.get("winter_break")
    if winter and day <= _date(winter) and (_date(winter) - day).days <= 90:
        lines.append("放寒假：%d 年 %d 月 %d 日（还有 %d 天）" % (_date(winter).year, _date(winter).month, _date(winter).day, (_date(winter) - day).days))
    sports = term.get("sports_meet")
    if sports and day <= _date(sports[0]) and (_date(sports[0]) - day).days <= 60:
        lines.append("校运动会：%d 月 %d-%d 日" % (_date(sports[0]).month, _date(sports[0]).day, _date(sports[1]).day))
    summer = term.get("summer_break")
    if summer and day <= _date(summer) and (_date(summer) - day).days <= 60:
        lines.append("放暑假：%d 月 %d 日" % (_date(summer).month, _date(summer).day))
    return lines


def context_text(now, root, school=None, major=None, grade=None, city=None, note=None, courses=None):
    """生成给模型看的「现实背景」文本，每轮都会重新计算。"""
    day = now.date() if isinstance(now, datetime) else now
    school = school or SCHOOL["name"]
    city = city or SCHOOL["city"]
    major = major or "软件工程"
    grade = grade or "大一"
    holidays = load_holidays(root)
    term = _term_of(day)
    stage, stage_detail = _stage_of(day, term, holidays)

    lines = [
        "今天是 %d 年 %d 月 %d 日，星期%s。你在%s读%s（%s，%s），人在%s。"
        % (day.year, day.month, day.day, WEEKDAYS[day.weekday()], school, major, grade, school, city),
        "当前阶段：%s。%s" % (stage, stage_detail),
    ]

    item = holidays.get(day.strftime("%Y-%m-%d"))
    if item and item.get("is_holiday"):
        lines.append(
            "今天是%s，放假第 %d 天，不用上课（学校可能安排调课）。"
            % (item.get("name", "假期"), _holiday_days_before(day, holidays) + 1)
        )
    elif item and not item.get("is_holiday"):
        lines.append("注意：今天是%s的调休补课日，要上课。" % item.get("name", "调休"))
    else:
        holiday_day, holiday_name = _next_holiday(day, holidays)
        if holiday_day and (holiday_day - day).days <= 21:
            length = _holiday_length(holiday_day, holidays)
            lines.append(
                "离你最近的是%s：%d 月 %d 日开始放 %d 天（%d 天后开始）。"
                % (
                    holiday_name,
                    holiday_day.month,
                    holiday_day.day,
                    length,
                    (holiday_day - day).days,
                )
            )

    dates = key_dates(day, term)
    if dates:
        lines.append("近期关键日期：" + "；".join(dates) + "。")

    unpublished = unpublished_holiday_note(day, holidays)
    if unpublished:
        lines.append(unpublished)

    if term:
        lines.append("学期：%s。" % term["name"])
    lines.append("作息：%s。" % (note or SCHEDULE_TEXT))
    lines.append(course_line(day, term, major, courses))
    lines.append(
        "重要：聊天记录里高三的内容（模考、晚自习、班主任、高考）是过去，只有回忆或对方提起时才聊；"
        "你现在的生活是大学——军训、上课、宿舍、社团、选课、食堂、期末。不要把自己说成高中生。"
    )
    return "\n".join(lines)


def stage_name(now, root=None):
    day = now.date() if isinstance(now, datetime) else now
    holidays = load_holidays(root) if root else {}
    stage, _ = _stage_of(day, _term_of(day), holidays)
    return stage


def quick_facts(now, root, major=None):
    """几条"此刻确定知道的事"。

    单独抽出来是为了把它放在提示词**最后**（紧挨着对方的消息）——
    实测放在前面会被几千字的档案稀释，模型回答时想不起来用。
    """
    day = now.date() if isinstance(now, datetime) else now
    holidays = load_holidays(root)
    term = _term_of(day)
    facts = []

    item = holidays.get(day.strftime("%Y-%m-%d"))
    if item and item.get("is_holiday"):
        facts.append("今天是%s，放假（第 %d 天）" % (item.get("name"), _holiday_days_before(day, holidays) + 1))

    # 法定节假日：日期是国家统一规定的，直接说，不许说"等通知"
    for name, start, end, days, after in upcoming_major_holidays(day, holidays, count=3):
        facts.append(
            "%s：%d 月 %d 日 - %d 月 %d 日放假 %d 天（%d 天后开始）"
            % (name, start.month, start.day, end.month, end.day, days, after)
        )

    if term:
        training = term.get("military_training")
        if training:
            end = _date(training[1])
            if day <= end:
                facts.append("军训 %d 月 %d 日结束，还剩 %d 天" % (end.month, end.day, (end - day).days))
        class_start = term.get("class_start")
        if class_start and day <= _date(class_start):
            target = _date(class_start)
            facts.append("正式上课 %d 月 %d 日开始（还有 %d 天）" % (target.month, target.day, (target - day).days))
        winter = term.get("winter_break")
        if winter:
            target = _date(winter)
            if 0 < (target - day).days <= 60:
                facts.append("放寒假 %d 月 %d 日（还有 %d 天）" % (target.month, target.day, (target - day).days))

    if not facts:
        return ""
    text = (
        "【你现在确定知道的几件事——被问到就直接答，不要说「不知道」「看情况」】\n"
        + "\n".join("- " + fact for fact in facts)
        + "\n- 法定节假日（春节、清明、劳动、端午、中秋、国庆、元旦）的放假日期是国家统一规定的，"
        "早就定好了。被问到这些，直接说日期和天数，绝对不能回答「还没通知」「看放不放」——那是常识。"
        "只有学校自己的调课安排、考试安排这类，才可以说不确定。"
    )
    unpublished = unpublished_holiday_note(day, holidays)
    if unpublished:
        text += "\n- " + unpublished
    return text
