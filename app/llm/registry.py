"""模型路由 + Provider 注册表。

请求里的 model 支持两种写法：
- `mock/demo`
- `provider/model`，例如 `ollama/qwen2.5:7b`、`openai/gpt-4o-mini`
- 只写 `model`（如 `qwen2.5:7b`）时，使用 DEFAULT_PROVIDER。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app.config import Settings
from app.errors import ProviderNotConfiguredError
from app.llm.base import BaseLLM
from app.llm.mock import MockLLM
from app.llm.openai_compat import OpenAICompatibleLLM

KNOWN_PROVIDERS = ("mock", "openai", "oneapi", "ollama")


@dataclass(frozen=True)
class ModelRoute:
    requested: str
    provider: str
    upstream_model: str

    @property
    def canonical_id(self) -> str:
        if self.provider == "mock":
            return "mock/demo"
        return f"{self.provider}/{self.upstream_model}"


class LLMRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cache: Dict[str, BaseLLM] = {}

    def resolve(self, requested: Optional[str] = None) -> ModelRoute:
        raw = (requested or self.settings.default_model or "mock/demo").strip()
        if raw in {"mock", "demo", "mock/demo"}:
            return ModelRoute(requested=raw, provider="mock", upstream_model="demo")

        if "/" in raw:
            prefix, rest = raw.split("/", 1)
            if prefix in KNOWN_PROVIDERS:
                provider = prefix
                upstream = rest.strip()
            else:
                provider = self._default_provider()
                upstream = raw
        else:
            provider = self._default_provider()
            upstream = raw

        if provider == "mock":
            return ModelRoute(requested=raw, provider="mock", upstream_model="demo")
        if not upstream:
            upstream = self._default_model_for(provider)
        return ModelRoute(requested=raw, provider=provider, upstream_model=upstream)

    def _default_provider(self) -> str:
        provider = (self.settings.default_provider or "mock").strip().lower()
        return provider if provider in KNOWN_PROVIDERS else "mock"

    def _default_model_for(self, provider: str) -> str:
        return {
            "openai": self.settings.openai_model,
            "oneapi": self.settings.oneapi_model,
            "ollama": self.settings.ollama_model,
            "mock": "demo",
        }.get(provider, "demo")

    def get(self, requested: Optional[str] = None) -> Tuple[BaseLLM, ModelRoute]:
        route = self.resolve(requested)
        cache_key = f"{route.provider}:{route.upstream_model}"
        if cache_key in self._cache:
            return self._cache[cache_key], route

        if route.provider == "mock":
            llm: BaseLLM = MockLLM()
        elif route.provider == "openai":
            if not self.settings.openai_api_key.strip():
                raise ProviderNotConfiguredError(
                    "未配置 OPENAI_API_KEY。请在 .env 中填写，或改用 mock/demo / ollama/* 模型。"
                )
            llm = OpenAICompatibleLLM(
                provider="openai",
                model=route.upstream_model,
                base_url=self.settings.openai_base_url,
                api_key=self.settings.openai_api_key,
                timeout_seconds=self.settings.request_timeout_seconds,
                max_retries=self.settings.provider_max_retries,
            )
        elif route.provider == "oneapi":
            if not self.settings.oneapi_api_key.strip():
                raise ProviderNotConfiguredError(
                    "未配置 ONEAPI_API_KEY。OneAPI / New API 需要填写代理服务的 Key。"
                )
            llm = OpenAICompatibleLLM(
                provider="oneapi",
                model=route.upstream_model,
                base_url=self.settings.oneapi_base_url,
                api_key=self.settings.oneapi_api_key,
                timeout_seconds=self.settings.request_timeout_seconds,
                max_retries=self.settings.provider_max_retries,
            )
        elif route.provider == "ollama":
            llm = OpenAICompatibleLLM(
                provider="ollama",
                model=route.upstream_model,
                base_url=self.settings.ollama_base_url,
                api_key="",  # Ollama 默认不校验 Key
                timeout_seconds=self.settings.request_timeout_seconds,
                max_retries=self.settings.provider_max_retries,
            )
        else:  # pragma: no cover - resolve 已限制
            raise ProviderNotConfiguredError(f"未知 provider: {route.provider}")

        self._cache[cache_key] = llm
        return llm, route

    def list_models(self) -> List[Dict[str, str]]:
        models: List[Dict[str, str]] = [
            {"id": "mock/demo", "object": "model", "owned_by": "researchcrew-local"}
        ]
        if self.settings.openai_api_key.strip():
            models.append(
                {
                    "id": f"openai/{self.settings.openai_model}",
                    "object": "model",
                    "owned_by": "openai",
                }
            )
        if self.settings.oneapi_api_key.strip():
            models.append(
                {
                    "id": f"oneapi/{self.settings.oneapi_model}",
                    "object": "model",
                    "owned_by": "oneapi",
                }
            )
        if self.settings.ollama_base_url.strip():
            models.append(
                {
                    "id": f"ollama/{self.settings.ollama_model}",
                    "object": "model",
                    "owned_by": "ollama",
                }
            )
        # 去重，保持顺序
        seen = set()
        unique: List[Dict[str, str]] = []
        for item in models:
            if item["id"] not in seen:
                seen.add(item["id"])
                unique.append(item)
        return unique

    async def aclose(self) -> None:
        for llm in self._cache.values():
            await llm.aclose()
        self._cache.clear()
