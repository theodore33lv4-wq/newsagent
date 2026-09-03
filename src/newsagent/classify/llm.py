"""LLM 统一抽象与三个实现。

- OpenAICompatProvider：OpenAI 兼容的 chat/completions 协议（DeepSeek / 智谱 / 通义 / 千问等国内 API 均兼容）
- OllamaProvider：本地 Ollama（自带 /v1 OpenAI 兼容端点），无 GPU 机器不推荐
- MockLLMProvider：开发/无 Key 时验证流水线全链路；按系统提示词关键词返回确定性 JSON

工厂：create_provider(cfg) 依据 config.yaml 的 llm.provider 一行切换。
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx
from loguru import logger

from ..utils.config import Config, ConfigError


class LLMError(Exception):
    """LLM 调用最终失败（重试耗尽）。"""


class LLMProvider(ABC):
    """统一接口。chat 返回模型输出文本；json_mode=True 时请求结构化 JSON 输出。"""

    @abstractmethod
    def chat(self, messages: list[dict], *, json_mode: bool = False,
             temperature: float | None = None, model: str | None = None) -> str:
        raise NotImplementedError


class OpenAICompatProvider(LLMProvider):
    """OpenAI 兼容 HTTP 封装（httpx 直连，不依赖厂商 SDK）。"""

    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: float = 120.0, max_retries: int = 2,
                 temperature: float = 0.2):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.temperature = temperature

    def chat(self, messages: list[dict], *, json_mode: bool = False,
             temperature: float | None = None, model: str | None = None) -> str:
        body: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last_err: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=httpx.Timeout(self.timeout, connect=10.0)) as client:
                    resp = client.post(url, json=body, headers=headers)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    wait = 2.0 * (attempt + 1)
                    logger.warning("LLM 暂时性错误 {}，{:.0f}s 后重试 ({}/{})",
                                   resp.status_code, wait, attempt + 1, self.max_retries)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except Exception as exc:  # 网络/解析/4xx
                last_err = exc
                if attempt < self.max_retries:
                    wait = 2.0 * (attempt + 1)
                    logger.warning("LLM 调用失败({}): {}，{:.0f}s 后重试",
                                   type(exc).__name__, str(exc)[:200], wait)
                    time.sleep(wait)
                    continue
        raise LLMError(f"LLM 调用失败: {last_err}") from last_err


class OllamaProvider(OpenAICompatProvider):
    """本地 Ollama（http://localhost:11434/v1/chat/completions）。

    默认 model 为 qwen2.5:7b，可在 config.yaml 的 llm.model 中覆盖
    （注意：Ollama 的模型名形如 qwen2.5:7b，与云端模型名不同）。
    """

    def __init__(self, model: str = "qwen2.5:7b", **kwargs):
        super().__init__(
            base_url="http://localhost:11434/v1",
            api_key="ollama",  # 本地端点不校验
            model=model,
            timeout=kwargs.pop("timeout", 600.0),  # 本地推理慢，放宽超时
            **kwargs,
        )


class MockLLMProvider(LLMProvider):
    """确定性 Mock：按系统提示词内容返回可被下游解析的 JSON。

    - 系统提示词含「新闻打标」→ 返回单条打标结果
    - 系统提示词含「周报综述」→ 返回综述大纲
    - 其余 → {"ok": true}
    单元测试可显式传入 responder 覆盖默认行为。
    """

    def __init__(self, responder=None):
        self._responder = responder

    def chat(self, messages: list[dict], *, json_mode: bool = False,
             temperature: float | None = None, model: str | None = None) -> str:
        system = next((m["content"] for m in messages
                       if m.get("role") == "system"), "")
        if self._responder is not None:
            return self._responder(messages)
        if "新闻打标" in system:
            return json.dumps({
                "relevant": True,
                "tags": ["厂商动态/集成商动态"],
                "summary": "Mock 摘要：该新闻介绍某智能交通集成商中标信号控制项目。",
                "keywords": ["信号控制", "集成商", "中标"],
                "companies": ["中控信息", "银江技术"],
                "importance": 2,
            }, ensure_ascii=False)
        if "一句要点" in system:
            return json.dumps({
                "notes": [
                    {"idx": 1, "note": "试点城市扩大，车路云示范区扩容。"},
                    {"idx": 2, "note": "两家集成商披露中标。"},
                ],
            }, ensure_ascii=False)
        if "周报综述" in system:
            return json.dumps({
                "overview": "Mock 综述：本周智能交通领域动态聚焦车路协同与集成商中标。"
                            "政策端推动车路云一体化试点扩大，产业端多家集成商披露中标信息，"
                            "智慧高速建设进入机电改扩建密集期。",
                "overview_points": [
                    "政策：车路云一体化试点范围扩大",
                    "产业：两家集成商披露中标信息",
                    "城市：多地推进信号控制优化",
                ],
                "themes": [
                    {"title": "车路协同/智能网联", "items": [{"idx": 1, "note": "试点城市扩大。"}]},
                    {"title": "厂商动态", "items": [{"idx": 2, "note": "两家集成商中标。"}]},
                ],
                "top5": [1, 2],
                "trends": ["车路云一体化试验扩大"],
                "next_week": ["关注新一批试点名单"],
            }, ensure_ascii=False)
        return json.dumps({"ok": True}, ensure_ascii=False)


def create_provider(cfg: Config) -> LLMProvider:
    """按配置创建 LLM Provider（provider 一行切换）。"""
    name = cfg.llm.get("provider", "mock")
    model = cfg.llm.get("model", "deepseek-chat")
    temperature = float(cfg.llm.get("temperature", 0.2))
    timeout = float(cfg.llm.get("timeout_seconds", 120))
    max_retries = int(cfg.llm.get("max_retries", 2))

    if name == "mock":
        logger.debug("使用 MockLLMProvider（未接真实模型）")
        return MockLLMProvider()

    if name == "ollama":
        logger.debug("使用 OllamaProvider (model={})", model)
        return OllamaProvider(model=model, temperature=temperature,
                              timeout=timeout, max_retries=max_retries)

    # openai-compat（默认路径）
    api_key = cfg.llm_api_key
    if not api_key:
        raise ConfigError(
            f"llm.provider=openai-compat 但未配置 API Key：请在 .env 设置 "
            f"{cfg.llm.get('api_key_env', 'LLM_API_KEY')}（参照 .env.example）"
        )
    provider = OpenAICompatProvider(
        base_url=str(cfg.llm.get("base_url", "https://api.deepseek.com/v1")),
        api_key=api_key,
        model=model,
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
    )
    logger.debug("使用 OpenAICompatProvider (base_url={}, model={})",
                 cfg.llm.get("base_url"), model)
    return provider
