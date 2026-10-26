from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import routes_chat, routes_health, routes_models, routes_reports, routes_tools
from app.config import Settings, get_settings
from app.errors import ServiceError
from app.llm.registry import LLMRegistry
from app.reports.store import ReportStore
from app.service import ChatService
from app.tools.knowledge import KnowledgeBase, SearchService, TavilySearch
from app.tools.pdf import PDFExporter

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.reports_path.mkdir(parents=True, exist_ok=True)
        settings.knowledge_path.mkdir(parents=True, exist_ok=True)
        logger.info(
            "启动 %s v%s | model=%s | orchestrator=%s | auth=%s",
            settings.app_name,
            settings.app_version,
            settings.default_model,
            settings.orchestrator,
            settings.auth_enabled,
        )
        yield
        await app.state.registry.aclose()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "OpenAI 兼容的多智能体研究报告服务。"
            "默认使用离线 Mock 模型，可切换到 Ollama / OpenAI / OneAPI。"
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings

    registry = LLMRegistry(settings)
    reports = ReportStore(settings.reports_path)
    pdf = PDFExporter()
    knowledge = KnowledgeBase(settings.knowledge_path)
    tavily = (
        TavilySearch(settings.tavily_api_key, max_results=settings.tavily_max_results)
        if settings.tavily_api_key.strip()
        else None
    )
    search = SearchService(knowledge, tavily=tavily)
    service = ChatService(
        settings=settings,
        registry=registry,
        reports=reports,
        pdf=pdf,
        search=search,
    )
    app.state.registry = registry
    app.state.reports = reports
    app.state.pdf = pdf
    app.state.knowledge = knowledge
    app.state.search = search
    app.state.service = service

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=settings.cors_allow_credentials and settings.cors_origin_list != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
        logger.info(
            "%s %s -> %s %.1fms request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request_id,
        )
        return response

    @app.exception_handler(ServiceError)
    async def service_error_handler(request: Request, exc: ServiceError):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(status_code=exc.status_code, content=exc.to_openai(), headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        messages = []
        for item in exc.errors():
            location = ".".join(str(part) for part in item.get("loc", ()))
            messages.append(f"{location}: {item.get('msg', '参数错误')}")
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "；".join(messages) or "请求参数错误",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "invalid_request",
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "message": str(exc.detail),
                    "type": "invalid_request_error" if exc.status_code < 500 else "server_error",
                    "param": None,
                    "code": f"http_{exc.status_code}",
                }
            },
            headers=exc.headers,
        )

    app.include_router(routes_health.router)
    app.include_router(routes_chat.router)
    app.include_router(routes_models.router)
    app.include_router(routes_reports.router)
    app.include_router(routes_tools.router)

    static_dir = Path(__file__).resolve().parent / "static"
    if settings.enable_demo_ui and (static_dir / "index.html").exists():

        @app.get("/", include_in_schema=False)
        async def index():
            return FileResponse(static_dir / "index.html")

        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


app = create_app()
