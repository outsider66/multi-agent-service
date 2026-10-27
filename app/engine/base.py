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
class Revision:
    """审核未通过后的一次重写结果。

    单独建模而不是塞进 metadata，是因为它要同时被：
    - SSE 层编码成 `x_revision` 扩展帧（不影响标准 OpenAI 的 delta 流）；
    - 服务层拿去决定是否把草稿也落盘。
    """

    markdown: str
    reason: str = ""
    index: int = 1


@dataclass
class EngineResult:
    content: str
    usage: Dict[str, int] = field(default_factory=dict)
    phases: List[Dict[str, Any]] = field(default_factory=list)
    sources: List[Dict[str, Any]] = field(default_factory=list)
    research_preview: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    draft_content: str = ""  # 草稿版正文；审核通过（未重写）时为空


@dataclass
class EngineEvent:
    type: Literal["phase", "delta", "revision", "result"]
    phase: Optional[Phase] = None
    delta: str = ""
    revision: Optional[Revision] = None
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
