from __future__ import annotations

import time
from typing import AsyncIterator, Dict, List, Optional

from app.engine.base import BaseEngine, EngineEvent, EngineResult, Phase
from app.engine.prompts import SINGLE_SYSTEM
from app.llm.base import BaseLLM, estimate_messages_tokens, estimate_tokens


class SingleAgentEngine(BaseEngine):
    """普通聊天：一次模型调用，适合快速问答。"""

    name = "single"

    async def run(
        self,
        *,
        query: str,
        history: List[Dict[str, str]],
        llm: BaseLLM,
        temperature: float = 0.3,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[EngineEvent]:
        messages: List[Dict[str, str]] = []
        has_system = any(item.get("role") == "system" for item in history)
        if not has_system:
            messages.append({"role": "system", "content": SINGLE_SYSTEM})
        for item in history:
            role = item.get("role", "user")
            if role == "system":
                messages.append({"role": "system", "content": item.get("content", "")})
            elif role in {"user", "assistant"}:
                messages.append({"role": role, "content": item.get("content", "")})
        messages.append({"role": "user", "content": query})

        stage_start = time.perf_counter()
        yield EngineEvent(
            type="phase",
            phase=Phase(
                stage="generate", label="助手正在回答", detail="单 Agent 模式", agent="assistant"
            ),
        )

        pieces: List[str] = []
        async for delta in llm.stream(messages, temperature=temperature, max_tokens=max_tokens):
            pieces.append(delta)
            yield EngineEvent(type="delta", delta=delta)

        content = "".join(pieces)
        elapsed = int((time.perf_counter() - stage_start) * 1000)
        prompt_tokens = estimate_messages_tokens(messages)
        completion_tokens = estimate_tokens(content)
        yield EngineEvent(
            type="result",
            result=EngineResult(
                content=content,
                usage={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
                phases=[
                    Phase(
                        stage="generate",
                        label="回答完成",
                        detail=f"生成 {len(content)} 个字符",
                        agent="assistant",
                        elapsed_ms=elapsed,
                    ).public()
                ],
                metadata={"mode": "single", "agents": ["assistant"]},
            ),
        )
