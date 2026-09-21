#!/usr/bin/env python
"""独立小工具：把聊天记录解析成标准 jsonl，并打印说话人统计。

用法：
    python tools/import_chat.py 微信记录.txt --out work/chat.jsonl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from bot import chatlog  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="聊天记录解析与标准化")
    parser.add_argument("chat", help="聊天记录文件（txt / csv / json / jsonl）")
    parser.add_argument("--out", help="输出 jsonl 路径，默认 work/<文件名>.jsonl")
    parser.add_argument("--target", help="顺便统计某个人说了多少话、有哪些口头禅")
    args = parser.parse_args()

    path = Path(args.chat)
    if not path.exists():
        raise SystemExit("文件不存在：%s" % path)
    parsed = chatlog.load_chat_log(path)
    print("共解析 %d 条记录" % len(parsed.records))
    for speaker, count in parsed.speakers.most_common(20):
        print("  %-12s %d 条" % (speaker, count))
    kinds = {}
    for record in parsed.records:
        kinds[record.kind] = kinds.get(record.kind, 0) + 1
    print("消息类型分布：%s" % kinds)
    out = Path(args.out) if args.out else ROOT / "work" / (path.stem + ".jsonl")
    chatlog.to_jsonl(parsed.records, out)
    print("已导出：%s" % out)

    if args.target:
        from bot import style

        report = style.analyze(parsed.records, args.target)
        print()
        print(style.report_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
