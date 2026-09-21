#!/usr/bin/env python
"""微信性格克隆机器人的统一入口。

常用命令：
    python run.py init                              # 生成 config.json
    python run.py profile --chat 聊天记录.txt --me 昵称   # 从聊天记录生成人格
    python run.py chat                              # 终端里试聊（不碰微信）
    python run.py wechat --check                    # 检查微信连通性
    python run.py wechat                            # 接管个人微信（默认演练模式）
    python run.py wecom                             # 企业微信回调服务（合规通道）
    python run.py selftest                          # 离线自检
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def setup_console():
    """Windows 控制台默认是 GBK，聊天内容里的表情/生僻字会让 print 崩溃。
    这里换成 UTF-8 并对无法编码的字符降级，保证机器人不会因为一条消息挂掉。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 老版本 Python 或非标准流，忽略即可
            pass


setup_console()

from bot import chatlog  # noqa: E402
from bot import persona as persona_mod  # noqa: E402
from bot import style  # noqa: E402
from bot.config import DEFAULTS, load_config, project_path  # noqa: E402
from bot.engine import Engine  # noqa: E402
from bot.llm import MockLLM, build_llm, describe  # noqa: E402
from bot.memory import Memory  # noqa: E402
from bot.retriever import Retriever  # noqa: E402


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def resolve(path):
    path = Path(path)
    if path.is_absolute():
        return path
    if path.exists():
        return path.resolve()
    return (ROOT / path).resolve()


def print_header(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def clean_folder_arg(value):
    """清理拖拽进来的文件夹路径：去掉引号和结尾多余的反斜杠。

    从资源管理器把文件夹拖到 .cmd 上时，参数可能是 "E:\\截图\\" 这种形式，
    结尾的 \\" 会被命令行解析器当成转义引号，所以要统一收拾干净。
    """
    text = (value or "").strip().strip('"').strip()
    while len(text) > 3 and text.endswith("\\"):
        text = text[:-1]
    return text


def build_engine(cfg, args):
    persona_dir = resolve(getattr(args, "persona", None) or cfg["persona"]["dir"])
    loaded = persona_mod.load_persona(persona_dir, cfg)
    if not loaded["persona"]:
        raise SystemExit(
            "在 %s 里没找到 persona.json。\n先运行：python run.py profile --chat 你的聊天记录.txt --me 昵称"
            % persona_dir
        )
    cfg = copy.deepcopy(cfg)
    if getattr(args, "always_reply", False):
        cfg["behavior"]["ignore_probability"] = 0
        cfg["behavior"]["active_hours"] = [0, 24]
        cfg["behavior"]["cooldown_seconds"] = 0
    llm = build_llm(cfg)
    if getattr(args, "mock", False):
        llm = MockLLM()
    retriever = None
    if loaded["examples"] and cfg["retrieval"].get("enabled", True):
        retriever = Retriever(
            loaded["examples"],
            k=cfg["retrieval"].get("top_k", 4),
            max_pairs=cfg["retrieval"].get("max_pairs", 8000),
        )
    memory = None
    if not getattr(args, "no_memory", False):
        memory = Memory(
            project_path(cfg, cfg["memory"]["db"]),
            recent_turns=cfg["memory"].get("recent_turns", 12),
        )
    engine = Engine(
        cfg,
        persona=loaded["persona"],
        prompt=loaded["prompt"],
        retriever=retriever,
        llm=llm,
        memory=memory,
        name=loaded["persona"].get("name", "对方"),
    )
    if loaded["prompt"] and cfg["persona"].get("extra_rules"):
        engine.system_prompt = persona_mod.render_system_prompt(
            loaded["persona"], loaded["report"], cfg
        )
    print("已加载人格：%s（语料 %d 组）" % (engine.name, len(loaded["examples"])))
    print("模型：%s" % describe(llm))
    return engine, cfg


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_init(args):
    config_path = Path(args.config)
    if config_path.exists() and not args.force:
        print("config.json 已存在：%s（要覆盖请加 --force）" % config_path)
    else:
        example = ROOT / "config.example.json"
        if not example.exists():
            example.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8")
        shutil.copyfile(example, config_path)
        print("已生成 %s" % config_path)
    for folder in ("personas", "data", "work"):
        (ROOT / folder).mkdir(exist_ok=True)
    print("\n下一步：")
    print("  1) 打开 config.json，填 llm.api_key（也可以留空先用离线模拟模式）")
    print("  2) python run.py profile --chat 你的聊天记录.txt --me 要克隆的人的昵称")
    print("  3) python run.py chat")


def cmd_import(args):
    path = resolve(args.chat)
    parsed = chatlog.load_chat_log(path)
    print_header("解析结果：%s" % path)
    print("共 %d 条记录" % len(parsed.records))
    for speaker, count in parsed.speakers.most_common():
        print("  %s：%d 条" % (speaker, count))
    out = Path(args.out) if args.out else (ROOT / "work" / (path.stem + ".jsonl"))
    chatlog.to_jsonl(parsed.records, out)
    print("已导出标准格式：%s" % out)
    return 0


def _pick_target(parsed, me):
    speakers = parsed.speakers
    if not speakers:
        raise SystemExit("没解析出任何消息，请检查聊天记录格式（可以先跑 python run.py import --chat 文件）")
    if me:
        if me not in speakers:
            raise SystemExit(
                "聊天记录里没有「%s」。可选：%s" % (me, "、".join(speakers.keys()))
            )
        return me
    if "我" in speakers:
        return "我"
    target = speakers.most_common(1)[0][0]
    print("没有指定 --me，自动选定发言最多的人：%s" % target)
    print("可选发言者：%s" % "、".join("%s(%d条)" % (s, c) for s, c in speakers.most_common()))
    return target


def cmd_profile(args):
    cfg = load_config(args.config)
    path = resolve(args.chat)
    if not path.exists():
        raise SystemExit("找不到聊天记录文件：%s" % path)
    parsed = chatlog.load_chat_log(path)
    target = _pick_target(parsed, args.me)
    records = parsed.records
    texts_records = [r for r in records if r.sender == target and r.kind == "text"]
    if len(texts_records) < 5:
        print("警告：只有 %d 条可用发言，画像会很粗糙。建议至少几十条。" % len(texts_records))

    report = style.analyze(records, target)
    print_header("语言风格统计")
    print(style.report_text(report))

    pairs = chatlog.build_pairs(records, target, context_window=3)
    print("\n可用于参考的对话片段：%d 组" % len(pairs))

    llm = None
    if not args.no_llm:
        # 分析用更强的模型（config.json 里的 llm.analysis_model），
        # 日常回复仍用 llm.model，兼顾质量和成本
        llm = build_llm(cfg, role="analysis")
    print("\n模型：%s" % (describe(llm) if llm else "已禁用（--no-llm）"))

    heuristic = persona_mod.heuristic_persona(report, target)
    analyzed = None
    used_real_llm = llm is not None and not getattr(llm, "is_mock", False)
    if llm is not None:
        print("正在让模型分析性格……")
        analyzed = persona_mod.analyze_with_llm(
            llm, report, records, target, target, example_pairs=pairs[:20]
        )

    out_dir = resolve(args.out) if args.out else resolve(cfg["persona"]["dir"])
    if used_real_llm and analyzed is None and not getattr(args, "allow_degraded", False):
        # 大模型分析失败（断网、额度用尽等）：不要用"纯统计版"覆盖已经调好的人设
        if (out_dir / "persona.json").exists():
            print_header("大模型分析失败，已保留原有人设")
            print("原因通常是网络不通或账户额度不足。人设没有被改动，")
            print("修好之后重新投喂一次即可（或者用 python run.py profile 重跑）。")
            return 1

    persona = persona_mod.merge_persona(heuristic, analyzed)
    persona["name"] = target

    retriever = Retriever(pairs, k=cfg["retrieval"].get("top_k", 4), max_pairs=cfg["retrieval"].get("max_pairs", 8000))
    examples_text = ""
    probe = args.probe or "在吗"
    hits = retriever.search(probe, 4)
    if hits:
        examples_text = retriever.format_examples(hits, 4)
    prompt = persona_mod.render_system_prompt(persona, report, cfg, examples_text)

    persona_mod.save_persona(
        out_dir,
        persona,
        report,
        prompt,
        examples=pairs,
        meta={
            "target": target,
            "source": str(path),
            "records": len(records),
            "target_messages": len(texts_records),
            "pairs": len(pairs),
            "persona_source": persona.get("source", "heuristic"),
            "llm": describe(llm) if llm else "disabled",
        },
    )
    print_header("人格档案已生成：%s" % out_dir)
    print("一句话印象：%s" % persona.get("one_liner", ""))
    print("性格关键词：%s" % "；".join(persona.get("traits", [])[:5]))
    print("语言习惯：%s" % "；".join(persona.get("speech_habits", [])[:5]))
    print("口头禅：%s" % "、".join(persona.get("catchphrases", [])[:10]))
    print("\n生成的文件：")
    for name in ("persona.json", "system_prompt.txt", "style_report.txt", "style_report.json", "examples.json", "meta.json"):
        if (out_dir / name).exists():
            print("  %s" % (out_dir / name))
    print("\npersona.json 可以手动改：觉得哪条不像本人，直接编辑保存，下次启动就生效。")
    print("接着运行：python run.py chat")
    return 0


def cmd_chat(args):
    cfg = load_config(args.config)
    engine, cfg = build_engine(cfg, args)
    from bot.adapters.terminal import TerminalAdapter

    adapter = TerminalAdapter(cfg, engine, fast=args.fast)
    adapter.run()
    return 0


def cmd_feed(args):
    """投喂：把新截图自动变成语料并更新人格（一步完成）。"""
    from bot.feeder import default_state_path, feed, load_state

    cfg = load_config(args.config)
    state = load_state(default_state_path(ROOT))

    images = clean_folder_arg(args.folder or args.images) or state.get("images") or ""
    if not images:
        print_header("第一次投喂：先告诉我截图放在哪个文件夹")
        print("（把手机截图传到电脑后放进一个文件夹，然后把那个文件夹的路径粘进来）")
        try:
            images = clean_folder_arg(input("截图文件夹路径："))
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return 1

    target = (args.me or "").strip() or state.get("target") or ""
    if not target:
        try:
            target = input("要克隆谁？（聊天记录里的昵称，直接回车 = 对方）：").strip() or "对方"
        except (EOFError, KeyboardInterrupt):
            target = "对方"

    print_header("开始投喂")
    print("截图文件夹：%s" % images)
    print("克隆对象　：%s" % target)
    print("正在识别新截图（只处理没处理过的）……\n")

    summary = feed(
        cfg,
        ROOT,
        images=images,
        target=target,
        log=args.log,
        reset=args.reset,
    )
    print(summary["message"])
    if not summary["ok"]:
        return 1
    if summary["recognized"] == 0:
        print("没有新内容，人设保持不变。")
        return 0

    print("\n正在用最新语料重新生成人格……")
    profile_args = argparse.Namespace(
        chat=summary["log"],
        me=target,
        out=getattr(args, "out", None),
        probe=None,
        no_llm=False,
        allow_degraded=False,
        config=args.config,
    )
    return cmd_profile(profile_args)


def cmd_wechat(args):
    cfg = load_config(args.config)
    if args.live:
        cfg["behavior"]["dry_run"] = False
    engine, cfg = build_engine(cfg, args)
    from bot.adapters.wxauto_adapter import WxautoAdapter

    adapter = WxautoAdapter(cfg, engine)
    if args.check:
        print_header("微信连通性自检")
        adapter.check()
        return 0
    adapter.run()
    return 0


def cmd_qq(args):
    """QQ 接入（NapCat / OneBot v11）"""
    cfg = load_config(args.config)
    if args.live:
        cfg["behavior"]["dry_run"] = False
    if getattr(args, "dry", False):
        cfg["behavior"]["dry_run"] = True
    engine, cfg = build_engine(cfg, args)
    from bot.adapters.onebot import OneBotAdapter

    adapter = OneBotAdapter(cfg, engine)
    if args.check:
        print_header("QQ 连通性自检")
        return 0 if adapter.check() else 1
    adapter.run()
    return 0


def cmd_wecom(args):
    cfg = load_config(args.config)
    if args.live:
        cfg["behavior"]["dry_run"] = False
    engine, cfg = build_engine(cfg, args)
    from bot.adapters.wecom import WeComAdapter

    WeComAdapter(cfg, engine).run()
    return 0


def cmd_refresh(args):
    """更新节假日数据（每年国务院公布下一年安排后跑一次）。"""
    cfg = load_config(args.config)
    from bot.reality import refresh_holidays

    years = [int(item) for item in args.years.split(",")] if args.years else None
    print_header("更新节假日安排")
    result = refresh_holidays(ROOT, years=years)
    print("数据文件：%s" % result["file"])
    print("共 %d 条记录，本次更新 %d 条（年份：%s）"
          % (result["total"], result["updated"], "、".join(str(y) for y in result["years"])))
    return 0


def cmd_inspect(args):
    cfg = load_config(args.config)
    persona_dir = resolve(args.persona or cfg["persona"]["dir"])
    loaded = persona_mod.load_persona(persona_dir, cfg)
    if not loaded["persona"]:
        raise SystemExit("没找到人格文件：%s" % persona_dir)
    print_header("人格档案：%s" % persona_dir)
    print(json.dumps(loaded["persona"], ensure_ascii=False, indent=2))
    print("\n语料片段：%d 组" % len(loaded["examples"]))
    print("\n" + "=" * 60)
    print("系统提示词（真正约束模型行为的东西）")
    print("=" * 60)
    print(loaded["prompt"] or persona_mod.render_system_prompt(loaded["persona"], loaded["report"], cfg))
    return 0


def cmd_selftest(args):
    print_header("离线自检（不联网、不碰微信）")
    passed, failed = [], []

    def check(name, func):
        try:
            detail = func()
            passed.append(name)
            print("  [通过] %s%s" % (name, ("  -> " + str(detail)) if detail else ""))
        except Exception as exc:  # noqa: BLE001
            failed.append((name, exc))
            print("  [失败] %s：%s" % (name, exc))
            if args.verbose:
                traceback.print_exc()

    sample = ROOT / "samples" / "sample_chat.txt"
    work = ROOT / "work"
    work.mkdir(exist_ok=True)
    state = {}

    def fresh_db():
        """每次自检都用全新的记忆库，保证反复运行结果一致。"""
        path = work / "selftest_memory.db"
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                Path(str(path) + suffix).unlink(missing_ok=True)
            except (OSError, TypeError):
                pass
        return path

    def test_parse():
        parsed = chatlog.load_chat_log(sample)
        state["parsed"] = parsed
        assert len(parsed.records) >= 20, "解析出的记录太少：%d" % len(parsed.records)
        assert len(parsed.speakers) >= 2, "应该至少有两个人说话"
        return "%d 条记录，%d 个说话人" % (len(parsed.records), len(parsed.speakers))

    def test_classify():
        kinds = set(r.kind for r in state["parsed"].records)
        assert "text" in kinds, "没有识别出文本消息"
        return "消息类型：%s" % "、".join(sorted(kinds))

    def test_style():
        target = state["parsed"].speakers.most_common(1)[0][0]
        report = style.analyze(state["parsed"].records, target)
        state["report"] = report
        state["target"] = target
        assert report["messages"] > 5, "样本太少"
        assert report["top_catchphrases"], "没有提炼出口头禅"
        return "%s：%d 条消息，口头禅 %d 个" % (target, report["messages"], len(report["top_catchphrases"]))

    def test_pairs():
        pairs = chatlog.build_pairs(state["parsed"].records, state["target"])
        state["pairs"] = pairs
        assert pairs, "没有构建出对话片段"
        return "%d 组对话片段" % len(pairs)

    def test_retrieval():
        retriever = Retriever(state["pairs"], k=3)
        hits = retriever.search("在吗")
        assert hits, "检索不到任何语料"
        state["retriever"] = retriever
        return "命中 %d 条，最高分 %.2f" % (len(hits), hits[0]["score"])

    def test_persona():
        heuristic = persona_mod.heuristic_persona(state["report"], state["target"])
        analyzed = persona_mod.analyze_with_llm(
            MockLLM(), state["report"], state["parsed"].records, state["target"], state["target"]
        )
        persona = persona_mod.merge_persona(heuristic, analyzed)
        state["persona"] = persona
        prompt = persona_mod.render_system_prompt(persona, state["report"], load_config(args.config))
        state["prompt"] = prompt
        assert persona["traits"], "没有性格关键词"
        out = persona_mod.save_persona(
            work / "selftest_persona", persona, state["report"], prompt, examples=state["pairs"]
        )
        assert (out / "persona.json").exists()
        return "%d 个字的关键词，提示词 %d 字" % (len(persona["traits"]), len(prompt))

    def test_engine():
        cfg = load_config(args.config)
        cfg["behavior"]["dry_run"] = True
        cfg["behavior"]["ignore_probability"] = 0
        cfg["behavior"]["active_hours"] = [0, 24]
        cfg["behavior"]["cooldown_seconds"] = 0
        memory = Memory(fresh_db(), recent_turns=8)
        engine = Engine(
            cfg,
            persona=state["persona"],
            prompt=state["prompt"],
            retriever=state["retriever"],
            llm=MockLLM(),
            memory=memory,
            name=state["target"],
        )
        plan = engine.plan("selftest", "朋友", "在吗在吗，晚上出来吃饭不")
        assert plan.send, "没有生成回复：%s" % plan.reason
        assert plan.chunks, "回复内容为空"
        assert all(d >= 0.5 for d in plan.delays), "打字延迟不合理"
        history = memory.recent("selftest", 10)
        assert len(history) >= 2, "记忆没有写入"
        state["plan"] = plan
        return "%d 条回复，延迟 %s 秒" % (len(plan.chunks), plan.delays)

    def test_memory_facts():
        memory = Memory(fresh_db(), recent_turns=8)
        fact = "对方在准备考研"
        assert memory.add_fact("selftest", fact), "写入长期记忆失败"
        memory.add_fact("selftest", fact)  # 重复写入应该被自动去重
        facts = memory.facts("selftest", 5)
        assert any("考研" in item for item in facts), "长期记忆没有读回来"
        assert len([item for item in facts if "考研" in item]) == 1, "同一件事被重复记住了"
        return "长期记忆可用（重复内容自动去重）"

    def test_sensitive_gate():
        cfg = load_config(args.config)
        cfg["behavior"]["ignore_probability"] = 0
        cfg["behavior"]["active_hours"] = [0, 24]
        engine = Engine(cfg, persona=state["persona"], prompt="x", llm=MockLLM(), name="测试")
        plan = engine.plan("gate", "朋友", "我这边急着用钱，能先借钱给我周转一下吗")
        assert not plan.send, "敏感消息不应该自动回复"
        assert "敏感" in plan.reason, "拦截原因不对：%s" % plan.reason
        return plan.reason

    def test_wecom_crypto():
        try:
            from Crypto.Cipher import AES  # noqa: F401
        except ImportError:
            return "跳过（未安装 pycryptodome，企业微信通道需要时才装）"
        import base64
        import os

        from bot.adapters.wecom import WeComCrypto, get_signature

        aes_key = base64.b64encode(os.urandom(32)).decode("utf-8")[:43]
        crypto = WeComCrypto("testtoken", aes_key, "corpid123")
        cipher_text = crypto.encrypt("<xml>你好</xml>")
        signature = get_signature("testtoken", "1700000000", "nonce1", cipher_text)
        assert crypto.verify(signature, "1700000000", "nonce1", cipher_text), "签名校验通过不了"
        assert not crypto.verify("bad" + signature, "1700000000", "nonce1", cipher_text), "错误签名竟然通过了"
        assert crypto.decrypt(cipher_text) == "<xml>你好</xml>", "解密结果不对"
        return "加解密 + 签名校验通过"

    check("解析聊天记录", test_parse)
    check("消息类型识别", test_classify)
    check("风格统计画像", test_style)
    check("对话片段构建", test_pairs)
    check("真实语料检索", test_retrieval)
    check("人格档案生成", test_persona)
    check("回复生成 + 记忆", test_engine)
    check("长期记忆", test_memory_facts)
    check("敏感话题拦截", test_sensitive_gate)
    check("企业微信加解密", test_wecom_crypto)

    print("\n结果：通过 %d 项，失败 %d 项" % (len(passed), len(failed)))
    if state.get("plan"):
        print("模拟回复示例：%s" % state["plan"].as_text())
    return 0 if not failed else 1


# --------------------------------------------------------------------------- #
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="微信性格克隆聊天机器人",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", default=str(ROOT / "config.json"), help="配置文件路径")
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="生成 config.json 和目录")
    p_init.add_argument("--force", action="store_true", help="覆盖已有 config.json")
    p_init.set_defaults(func=cmd_init)

    p_import = sub.add_parser("import", help="解析并导出聊天记录为标准格式")
    p_import.add_argument("--chat", required=True, help="聊天记录文件（txt/csv/json/jsonl）")
    p_import.add_argument("--out", help="导出路径（默认 work/文件.jsonl）")
    p_import.set_defaults(func=cmd_import)

    p_profile = sub.add_parser("profile", help="从聊天记录生成人格档案")
    p_profile.add_argument("--chat", required=True, help="聊天记录文件")
    p_profile.add_argument("--me", help="要克隆谁的说话风格（聊天记录里的昵称）")
    p_profile.add_argument("--out", help="输出目录（默认 personas/default）")
    p_profile.add_argument("--probe", help="用来挑选提示词示例的问句，默认「在吗」")
    p_profile.add_argument("--no-llm", action="store_true", help="只用本地统计，不调用大模型")
    p_profile.add_argument(
        "--allow-degraded",
        action="store_true",
        help="大模型分析失败时也保存纯统计版本（默认保留原有人设，避免越改越差）",
    )
    p_profile.set_defaults(func=cmd_profile)

    p_chat = sub.add_parser("chat", help="终端试聊")
    p_chat.add_argument("--persona", help="人格目录")
    p_chat.add_argument("--fast", action="store_true", help="不模拟打字延迟")
    p_chat.add_argument("--mock", action="store_true", help="强制离线模拟，不调用真实模型")
    p_chat.add_argument("--no-memory", action="store_true", help="不读写记忆")
    p_chat.add_argument("--always-reply", action="store_true", help="不随机忽略、不限时段")
    p_chat.set_defaults(func=cmd_chat)

    p_feed = sub.add_parser("feed", help="投喂新截图：自动识别 + 更新人格（一条命令搞定）")
    p_feed.add_argument("folder", nargs="?", help="截图文件夹（把文件夹拖到 投喂.cmd 上时会自动传进来）")
    p_feed.add_argument("--images", help="截图文件夹；也可以直接把文件夹拖到 投喂.cmd 上")
    p_feed.add_argument("--me", help="要克隆谁（默认沿用上次）")
    p_feed.add_argument("--log", help="累积语料文件（默认 work/聊天记录.txt）")
    p_feed.add_argument("--out", help="人格输出目录（默认 personas/default）")
    p_feed.add_argument("--reset", action="store_true", help="清空之前的语料，从头开始")
    p_feed.set_defaults(func=cmd_feed)

    p_wechat = sub.add_parser("wechat", help="接入个人微信（wxauto）")
    p_wechat.add_argument("--persona", help="人格目录")
    p_wechat.add_argument("--live", action="store_true", help="真的发消息（默认只演练）")
    p_wechat.add_argument("--check", action="store_true", help="只做连通性自检")
    p_wechat.add_argument("--mock", action="store_true", help="强制离线模拟")
    p_wechat.add_argument("--always-reply", action="store_true", help="不随机忽略、不限时段")
    p_wechat.set_defaults(func=cmd_wechat)

    p_qq = sub.add_parser("qq", aliases=["onebot"], help="接入 QQ（通过 NapCat 协议端）")
    p_qq.add_argument("--persona", help="人格目录")
    p_qq.add_argument("--live", action="store_true", help="真的发消息（默认只演练）")
    p_qq.add_argument("--dry", action="store_true", help="强制演练模式：只看它准备回什么，不真发")
    p_qq.add_argument("--check", action="store_true", help="只做连通性自检")
    p_qq.add_argument("--mock", action="store_true", help="强制离线模拟")
    p_qq.add_argument("--always-reply", action="store_true", help="不随机忽略、不限时段")
    p_qq.set_defaults(func=cmd_qq)

    p_wecom = sub.add_parser("wecom", help="启动企业微信回调服务")
    p_wecom.add_argument("--persona", help="人格目录")
    p_wecom.add_argument("--live", action="store_true", help="真的发消息")
    p_wecom.add_argument("--mock", action="store_true", help="强制离线模拟")
    p_wecom.add_argument("--always-reply", action="store_true", help="不随机忽略、不限时段")
    p_wecom.set_defaults(func=cmd_wecom)

    p_inspect = sub.add_parser("inspect", help="查看当前人格档案和系统提示词")
    p_inspect.add_argument("--persona", help="人格目录")
    p_inspect.set_defaults(func=cmd_inspect)

    p_refresh = sub.add_parser(
        "refresh",
        help="更新节假日数据（国务院公布下一年放假安排后跑一次）",
    )
    p_refresh.add_argument("--years", help="要更新的年份，例如 2026,2027；默认今年和明年")
    p_refresh.set_defaults(func=cmd_refresh)

    p_selftest = sub.add_parser("selftest", help="离线自检")
    p_selftest.add_argument("--verbose", action="store_true")
    p_selftest.set_defaults(func=cmd_selftest)

    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
