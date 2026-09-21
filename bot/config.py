"""配置加载：config.json + 环境变量覆盖。"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

DEFAULTS = {
    "llm": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-chat",
        "analysis_model": "deepseek-reasoner",
        "temperature": 1.15,
        "timeout": 60,
        "max_retries": 2,
        "mock": False,
    },
    "persona": {
        "dir": "personas/default",
        "max_chars": 30,
        "split_long_replies": True,
        "identity_disclosure": False,
        "moods": [],
        "background": "",
        "time_awareness": True,
        "stage": "",
        "night_owl": True,
        # 默认值只是示例，真实信息写在 config.json（不入库）
        "school": "示例大学",
        "major": "计算机科学与技术",
        "grade": "大一",
        "city": "示例市",
        "schedule_note": "",
        "courses": [],
        "extra_rules": [],
    },
    "behavior": {
        "dry_run": True,
        "min_delay": 1.0,
        "max_delay": 6.0,
        "typing_chars_per_second": 6.0,
        "ignore_probability": 0.08,
        "cooldown_seconds": 2.0,
        "max_replies_per_minute": 12,
        "active_hours": [7, 3],
        "whitelist": [],
        "blacklist": [],
        "groups_enabled": False,
        "groups_require_at": True,
        "sensitive_action": "handoff",
        "sensitive_keywords": [
            "借钱",
            "转账",
            "银行卡",
            "身份证",
            "验证码",
            "密码",
            "裸聊",
            "投资",
            "赌博",
            "毒品",
        ],
    },
    "memory": {
        "recent_turns": 18,
        "long_term": True,
        "summary": True,
        "summary_every": 12,
        "learn_feedback": True,
        "db": "data/memory.db",
    },
    "retrieval": {"enabled": False, "top_k": 2, "max_pairs": 8000},
    "wechat": {"adapter": "wxauto", "poll_interval": 1.2, "self_names": []},
    "onebot": {
        "api_base": "http://127.0.0.1:3000",
        "access_token": "",
        "self_id": "",
        "host": "127.0.0.1",
        "port": 5700,
        "path": "/onebot",
    },
    "wecom": {
        "corp_id": "",
        "agent_id": "",
        "secret": "",
        "token": "",
        "encoding_aes_key": "",
        "host": "0.0.0.0",
        "port": 8000,
    },
}

# 环境变量 -> 配置路径
ENV_OVERRIDES = {
    "LLM_API_KEY": ("llm", "api_key"),
    "LLM_BASE_URL": ("llm", "base_url"),
    "LLM_MODEL": ("llm", "model"),
    "WECHAT_PERSONA_DIR": ("persona", "dir"),
}


def _deep_merge(base, override):
    """把 override 合并进 base 的副本，只对 dict 递归。"""
    result = copy.deepcopy(base)
    if not isinstance(override, dict):
        return result
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _expand_env(value):
    """支持 "${ENV_VAR}" / "$ENV_VAR" 写法，方便把密钥放在环境变量里。"""
    if not isinstance(value, str) or not value.startswith("$"):
        return value
    name = value.strip("${}").strip()
    return os.environ.get(name, "")


def load_config(path="config.json"):
    cfg = copy.deepcopy(DEFAULTS)
    cfg_path = Path(path)
    if cfg_path.exists():
        try:
            user_cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            raise SystemExit("config.json 不是合法 JSON：%s" % exc)
        cfg = _deep_merge(cfg, user_cfg)
    for env_name, (section, key) in ENV_OVERRIDES.items():
        if os.environ.get(env_name):
            cfg.setdefault(section, {})[key] = os.environ[env_name]
    cfg["llm"]["api_key"] = _expand_env(cfg["llm"].get("api_key", ""))
    for key in ("secret", "token", "encoding_aes_key", "corp_id", "agent_id"):
        cfg["wecom"][key] = _expand_env(cfg["wecom"].get(key, ""))
    cfg["_path"] = str(cfg_path.resolve())
    cfg["_root"] = str(cfg_path.resolve().parent)
    return cfg


def project_path(cfg, relative):
    """把配置里的相对路径解析成项目内的绝对路径。"""
    path = Path(relative)
    if path.is_absolute():
        return path
    return Path(cfg.get("_root", ".")) / path


def ensure_runtime_dirs(cfg):
    """建好运行期需要的目录。"""
    for relative in (cfg["persona"]["dir"], str(Path(cfg["memory"]["db"]).parent), "data"):
        project_path(cfg, relative).mkdir(parents=True, exist_ok=True)
