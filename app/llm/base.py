"""LLM 提供方抽象层。

为什么不用 OpenAI Python SDK 直接写死？
- 方便展示“协议”本身：OpenAI、OneAPI、Ollama 的兼容端点都可以用同一套 HTTP 逻辑；
- 方便测试：注入 httpx.MockTransport 就能在没有网络的情况下验证请求和 SSE 解析；
- 方便替换：以后接 vLLM、DashScope、智谱，只需要新增一个 provider 配置。
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.errors import ProviderError


@dataclass
class LLMResult:
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str = "stop"
    upstream_model: Optional[str] = None


class LLMError(ProviderError):
    """上游模型调用失败。"""


class BaseLLM(ABC):
    provider: str = "base"
    upstream_model: str = ""

    @abstractmethod
    async def complete(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: Optional[int] = None,
    ) -> LLMResult:
        raise NotImplementedError

    @abstractmethod
    async def stream(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: Optional[int] = None,
    ):
        """异步生成器：逐段 yield 文本增量。"""
        raise NotImplementedError
        yield ""  # pragma: no cover - 让类型检查器知道这是异步生成器

    async def aclose(self) -> None:
        return None


def estimate_tokens(text: str) -> int:
    """轻量 token 估算，仅用于展示 usage，不用于计费。

    中文大致 1 字 ≈ 1 token，英文约 4 字符 ≈ 1 token。真实计费请以上游 usage 为准。
    """
    if not text:
        return 0
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    rest = len(text) - cjk
    return max(1, cjk + max(1, rest // 4))


def estimate_messages_tokens(messages: List[Dict[str, str]]) -> int:
    # 每条消息有少量角色/分隔符开销
    return sum(estimate_tokens(item.get("content", "")) + 4 for item in messages)


def content_to_text(content) -> str:
    """兼容部分兼容层返回的多段 content。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
        return "".join(parts)
    return ""
