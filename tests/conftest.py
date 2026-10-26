from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        _env_file=None,
        environment="test",
        data_dir=tmp_path,
        knowledge_dir=PROJECT_ROOT / "data" / "knowledge",
        reports_dir=tmp_path / "reports",
        default_model="mock/demo",
        default_provider="mock",
        api_key="",
        orchestrator="builtin",
        tavily_api_key="",
        enable_demo_ui=True,
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def app(tmp_path):
    return create_app(make_settings(tmp_path))


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_client(tmp_path):
    app = create_app(make_settings(tmp_path, api_key="test-secret"))
    with TestClient(app) as test_client:
        yield test_client
