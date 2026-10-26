"""应用配置。

设计原则：
1. 所有可变配置都从环境变量 / .env 读取，代码里只保留安全的默认值；
2. 默认模型是 mock/demo，保证没有 API Key、没有 Ollama 时也能启动；
3. 配置对象通过 create_app(settings) 注入，测试可以传入临时目录，不污染真实数据。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "ResearchCrew"
    app_version: str = "0.1.0"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # ---- 服务鉴权 ----
    # 留空 = 本地开发不鉴权；线上必须设置。
    api_key: str = ""

    # ---- 模型路由 ----
    default_model: str = "mock/demo"
    default_provider: str = "mock"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    oneapi_api_key: str = ""
    oneapi_base_url: str = "http://localhost:3000/v1"
    oneapi_model: str = "gpt-4o-mini"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "qwen2.5:7b"

    request_timeout_seconds: float = Field(default=120.0, gt=0)
    provider_max_retries: int = Field(default=1, ge=0, le=5)

    # ---- 检索 ----
    tavily_api_key: str = ""
    tavily_max_results: int = Field(default=4, ge=1, le=10)

    # ---- 存储 ----
    data_dir: Path = PROJECT_ROOT / "data"
    knowledge_dir: Optional[Path] = None
    reports_dir: Optional[Path] = None
    max_report_chars: int = Field(default=30000, ge=1000)

    # ---- 编排 ----
    orchestrator: Literal["builtin", "crewai"] = "builtin"

    # ---- Web ----
    cors_origins: str = "*"
    cors_allow_credentials: bool = False
    enable_demo_ui: bool = True

    @property
    def knowledge_path(self) -> Path:
        return self.knowledge_dir or (self.data_dir / "knowledge")

    @property
    def reports_path(self) -> Path:
        return self.reports_dir or (self.data_dir / "reports")

    @property
    def cors_origin_list(self) -> List[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_key.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程级默认配置。测试中请直接用 create_app(Settings(...)) 注入。"""
    return Settings()
