from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app.config import Settings
from app.llm.registry import LLMRegistry

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(request: Request):
    settings: Settings = request.app.state.settings
    registry: LLMRegistry = request.app.state.registry
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "orchestrator": settings.orchestrator,
        "default_model": registry.resolve(settings.default_model).canonical_id,
        "auth_enabled": settings.auth_enabled,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/readyz")
async def readyz(request: Request):
    settings: Settings = request.app.state.settings
    checks = {
        "reports_dir": settings.reports_path.exists(),
        "knowledge_dir": settings.knowledge_path.exists(),
    }
    return {"status": "ready" if all(checks.values()) else "degraded", "checks": checks}
