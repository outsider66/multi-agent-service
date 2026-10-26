"""Agent 编排接口。

Engine 只负责“如何协作”，不负责 HTTP、鉴权、报告存储。
这样同一个 Engine 既可以被 FastAPI 调用，也可以被 scripts/demo.py 直接调用。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Literal, Optional

from app.llm.base import BaseLLM


@dataclass
class Phase:
    stage: str
    label: str
    detail: str = ""
    agent: str = ""
    elapsed_ms: int = 0

    def public(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "label": self.label,
            "detail": self.detail,
            "agent": self.agent,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class EngineResult:
    content: str
    usage: Dict[str, int] = field(default_factory=dict)
    phases: List[Dict[str, Any]] = field(default_factory=list)
    sources: List[Dict[str, Any]] = field(default_factory=list)
    research_preview: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineEvent:
    type: Literal["phase", "delta", "result"]
    phase: Optional[Phase] = None
    delta: str = ""
    result: Optional[EngineResult] = None


class BaseEngine(ABC):
    name = "base"

    @abstractmethod
    async def run(
        self,
        *,
        query: str,
        history: List[Dict[str, str]],
        llm: BaseLLM,
        temperature: float = 0.3,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[EngineEvent]:
        raise NotImplementedError
        yield EngineEvent(type="result")  # pragma: no cover


def history_to_text(history: List[Dict[str, str]], limit: int = 8) -> str:
    if not history:
        return "（无）"
    lines: List[str] = []
    for item in history[-limit:]:
        role = item.get("role", "user")
        content = item.get("content", "").strip()
        if not content:
            continue
        if len(content) > 1200:
            content = content[:1200] + "……"
        lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "（无）"
