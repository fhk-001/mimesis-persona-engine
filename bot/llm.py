"""大模型调用层：任何 OpenAI 兼容接口都能用（DeepSeek / 通义 / Kimi / 智谱 / OpenAI / 本地 Ollama）。

只依赖 Python 标准库，不需要额外安装包。
"""

from __future__ import annotations

import json
import random
import re
import time
import urllib.error
import urllib.request

from .sanitize import clean


class LLMError(RuntimeError):
    pass


class LLM:
    """OpenAI 兼容的 chat/completions 客户端。"""

    def __init__(
        self,
        base_url="",
        api_key="",
        model="",
        temperature=1.0,
        timeout=60,
        max_retries=2,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        if not self.base_url:
            self.endpoint = ""
        elif self.base_url.endswith("/chat/completions"):
            self.endpoint = self.base_url
        else:
            self.endpoint = self.base_url + "/chat/completions"

    @property
    def available(self):
        return bool(self.api_key and self.endpoint and self.model)

    def _payload(self, messages, temperature, json_mode, max_tokens):
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if max_tokens:
            payload["max_tokens"] = int(max_tokens)
        return payload

    def chat(self, messages, temperature=None, json_mode=False, max_tokens=None):
        if not self.available:
            raise LLMError("没有配置 API Key 或模型名，无法调用大模型")
        use_json = bool(json_mode)
        last_error = None
        for attempt in range(self.max_retries + 1):
            payload = self._payload(messages, temperature, use_json, max_tokens)
            try:
                return self._request(payload)
            except LLMError as exc:
                last_error = exc
                message = str(exc)
                if use_json and ("response_format" in message or "json_object" in message):
                    use_json = False
                    continue
                if "HTTP 401" in message or "HTTP 403" in message:
                    raise
                if attempt < self.max_retries:
                    time.sleep(1.5 * (attempt + 1))
        raise last_error if last_error else LLMError("调用失败")

    def _request(self, payload):
        # 兜底清洗：万一上下文里混进了代理字符，直接发给接口会抛异常
        body = clean(json.dumps(payload, ensure_ascii=False)).encode("utf-8")
        request = urllib.request.Request(self.endpoint, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", "Bearer " + self.api_key)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:400]
            except Exception:  # noqa: BLE001 - 读取错误详情失败不影响主流程
                pass
            raise LLMError("HTTP %s: %s" % (exc.code, detail)) from exc
        except Exception as exc:  # noqa: BLE001 - 网络层任何异常统一抛出
            raise LLMError("请求失败: %s" % exc) from exc
        return self._parse_response(raw)

    @staticmethod
    def _parse_response(raw):
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise LLMError("接口返回的不是 JSON：%s" % raw[:200]) from exc
        if isinstance(data, dict) and data.get("error"):
            raise LLMError("接口报错：%s" % str(data["error"])[:300])
        try:
            choice = data["choices"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("接口返回缺少 choices：%s" % raw[:200]) from exc
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        return (content or "").strip()


class MockLLM:
    """离线模式：不联网也能把整条链路跑通，用来验证流程和语料。"""

    available = True
    is_mock = True

    def __init__(self, samples=None):
        self.samples = samples or [
            "嗯嗯",
            "真的假的哈哈哈",
            "好呀",
            "那我晚点跟你说",
            "你先忙你的~",
            "哈哈哈笑死",
        ]

    def chat(self, messages, temperature=None, json_mode=False, max_tokens=None):
        seed = 0
        for message in messages:
            content = str(message.get("content", ""))
            seed = (seed * 31 + sum(ord(char) for char in content) + len(content)) % 1000003
        if json_mode:
            blob = " ".join(str(message.get("content", "")) for message in messages)
            if '"messages"' in blob:
                first = self.samples[seed % len(self.samples)]
                second = self.samples[(seed + 3) % len(self.samples)]
                return json.dumps({"messages": [first, second]}, ensure_ascii=False)
            return json.dumps(
                {"traits": ["离线模拟模式生成的占位画像（配好 API Key 后重新 profile 即可）"]},
                ensure_ascii=False,
            )
        return self.samples[seed % len(self.samples)]


def build_llm(cfg, role="chat"):
    """根据配置返回真实模型或离线模拟模型。

    role="chat"     日常回复用的模型（要求快、便宜）
    role="analysis" 分析语料、提炼人格用的模型（可以用更强的推理模型）
    """
    llm_cfg = cfg.get("llm", {})
    mock = bool(llm_cfg.get("mock"))
    api_key = llm_cfg.get("api_key") or ""
    if mock or not api_key:
        return MockLLM()
    model = llm_cfg.get("model", "")
    if role == "analysis" and llm_cfg.get("analysis_model"):
        model = llm_cfg["analysis_model"]
    return LLM(
        base_url=llm_cfg.get("base_url", ""),
        api_key=api_key,
        model=model,
        temperature=llm_cfg.get("temperature", 1.0),
        timeout=llm_cfg.get("timeout", 60),
        max_retries=llm_cfg.get("max_retries", 2),
    )


def describe(llm):
    if getattr(llm, "is_mock", False):
        return "离线模拟模式（未配置 API Key，回复内容不代表模型能力）"
    return "模型：%s @ %s" % (llm.model, llm.base_url)


def extract_json(text):
    """从模型输出里抠出 JSON，容忍 ``` 包裹和尾随逗号。"""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    candidate = cleaned[start : end + 1]
    try:
        return json.loads(candidate)
    except ValueError:
        repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            return json.loads(repaired)
        except ValueError:
            return None


def pick_random(items, default=""):
    if not items:
        return default
    return random.choice(items)
