from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import get_service, require_api_key
from app.errors import ServiceError
from app.schemas import ChatCompletionRequest, new_completion_id
from app.service import ChatResult, ChatService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"], dependencies=[Depends(require_api_key)])


def _sse(payload: Dict[str, Any]) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def _chunk(
    *,
    completion_id: str,
    created: int,
    model: str,
    delta: Dict[str, Any],
    finish_reason: Any = None,
    usage: Any = None,
    phase: Any = None,
    agent: Any = None,
    revision: Any = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    if usage is not None:
        payload["usage"] = usage
    if phase is not None:
        payload["x_phase"] = phase
    if agent is not None:
        payload["x_agent"] = agent
    if revision is not None:
        # 扩展字段：标准 OpenAI 客户端会忽略它，因此只看到草稿正文，不会串台
        payload["x_revision"] = revision
    return payload


@router.post("/v1/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest,
    request: Request,
    service: ChatService = Depends(get_service),
):
    prepared = service.prepare(body)
    completion_id = new_completion_id()
    created = int(time.time())

    if not body.stream:
        result = await service.complete(prepared)
        return JSONResponse(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": result.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": result.content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": result.usage,
                "x_agent": result.agent_metadata(),
            }
        )

    async def event_stream() -> AsyncIterator[str]:
        # 第一条 chunk 先声明 role，符合 OpenAI 流式客户端习惯。
        yield _sse(
            _chunk(
                completion_id=completion_id,
                created=created,
                model=prepared.route.canonical_id,
                delta={"role": "assistant", "content": ""},
            )
        )
        cancelled = False
        try:
            async for event in service.stream(prepared):
                if event.type == "phase":
                    yield _sse(
                        _chunk(
                            completion_id=completion_id,
                            created=created,
                            model=prepared.route.canonical_id,
                            delta={},
                            phase=event.phase,
                        )
                    )
                elif event.type == "delta":
                    yield _sse(
                        _chunk(
                            completion_id=completion_id,
                            created=created,
                            model=prepared.route.canonical_id,
                            delta={"content": event.delta},
                        )
                    )
                elif event.type == "revision":
                    # delta 留空：正文流只承载草稿，修改版走 x_revision
                    yield _sse(
                        _chunk(
                            completion_id=completion_id,
                            created=created,
                            model=prepared.route.canonical_id,
                            delta={},
                            revision=event.revision,
                        )
                    )
                elif event.type == "done" and event.result is not None:
                    result: ChatResult = event.result
                    yield _sse(
                        _chunk(
                            completion_id=completion_id,
                            created=created,
                            model=result.model,
                            delta={},
                            finish_reason="stop",
                            usage=result.usage,
                            agent=result.agent_metadata(),
                        )
                    )
        except asyncio.CancelledError:
            # 客户端主动断开：不要伪装成成功，也不要把取消异常吞掉。
            cancelled = True
            raise
        except Exception as exc:
            logger.exception(
                "流式生成失败 request_id=%s", getattr(request.state, "request_id", "-")
            )
            if isinstance(exc, ServiceError):
                error = exc.to_openai()
            else:
                error = {
                    "error": {
                        "message": "流式生成失败，请查看服务端日志",
                        "type": "server_error",
                        "param": None,
                        "code": "stream_error",
                    }
                }
            yield "event: error\ndata: " + json.dumps(error, ensure_ascii=False) + "\n\n"
        finally:
            if not cancelled:
                yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
