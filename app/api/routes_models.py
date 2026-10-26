from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request

from app.api.deps import require_api_key
from app.llm.registry import LLMRegistry

router = APIRouter(tags=["models"], dependencies=[Depends(require_api_key)])


@router.get("/v1/models")
async def list_models(request: Request):
    registry: LLMRegistry = request.app.state.registry
    created = int(time.time())
    data = []
    for item in registry.list_models():
        item = dict(item)
        item.setdefault("created", created)
        data.append(item)
    return {"object": "list", "data": data}
