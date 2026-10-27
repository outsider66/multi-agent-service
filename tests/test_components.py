from __future__ import annotations

import asyncio
import json

import fitz
import httpx
import pytest

from app.config import PROJECT_ROOT, Settings
from app.errors import BadRequestError, ProviderNotConfiguredError
from app.llm.mock import MockLLM
from app.llm.openai_compat import OpenAICompatibleLLM
from app.llm.registry import LLMRegistry
from app.reports.store import ReportStore
from app.tools.knowledge import KnowledgeBase
from app.tools.pdf import PDFExporter


def test_model_routing_and_missing_configuration():
    settings = Settings(
        _env_file=None,
        default_model="ollama/qwen2.5:7b",
        default_provider="ollama",
        openai_api_key="",
        oneapi_api_key="",
    )
    registry = LLMRegistry(settings)
    route = registry.resolve()
    assert route.provider == "ollama"
    assert route.upstream_model == "qwen2.5:7b"
    assert registry.resolve("qwen2.5:7b").provider == "ollama"
    assert registry.resolve("mock").canonical_id == "mock/demo"

    with pytest.raises(ProviderNotConfiguredError):
        registry.get("openai/gpt-4o-mini")

def test_vllm_provider_routing_and_client():
    # 配置齐全的场景：验证路由解析、实例类型、请求地址、模型列表暴露
    settings = Settings(
        _env_file=None,
        default_provider="mock",
        vllm_base_url="http://127.0.0.1:8001/v1",
        vllm_model="qwen2:latest",
        openai_api_key="",
        oneapi_api_key="",
    )
    registry = LLMRegistry(settings)

    # 1. resolve：vllm/xxx 应被拆成 provider + upstream_model
    route = registry.resolve("vllm/qwen2:latest")
    assert route.provider == "vllm"
    assert route.upstream_model == "qwen2:latest"
    assert route.canonical_id == "vllm/qwen2:latest"

    # 2. resolve：只写 vllm/ 时应回落到 Settings.vllm_model
    fallback = registry.resolve("vllm/")
    assert fallback.upstream_model == "qwen2:latest"

    # 3. get：应拿到 OpenAICompatibleLLM，且 base_url 指向 vLLM
    llm, _ = registry.get("vllm/qwen2:latest")
    assert isinstance(llm, OpenAICompatibleLLM)
    assert llm.chat_url == "http://127.0.0.1:8001/v1/chat/completions"

    # 4. list_models：GET /v1/models 要能看到 vllm 模型
    ids = [item["id"] for item in registry.list_models()]
    assert "vllm/qwen2:latest" in ids

def test_vllm_missing_configuration():
    # 配置缺失的场景：base_url 为空时必须抛 ProviderNotConfiguredError
    settings = Settings(
        _env_file=None,
        vllm_base_url="",
        vllm_model="",
        openai_api_key="",
        oneapi_api_key="",
    )
    registry = LLMRegistry(settings)

    with pytest.raises(ProviderNotConfiguredError):
        registry.get("vllm/Qwen2.5-7B-Instruct")
def test_openai_compatible_client_complete_and_stream():
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured.append(payload)
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == "Bearer test-key"
        if payload["stream"]:
            body = (
                'data: {"choices":[{"delta":{"role":"assistant","content":""},"finish_reason":null}]}\n\n'
                'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"好"}}]}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "model": "demo-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "完整回答"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            },
        )

    async def scenario():
        llm = OpenAICompatibleLLM(
            provider="test",
            model="demo-model",
            base_url="http://example.test/v1",
            api_key="test-key",
            max_retries=0,
            transport=httpx.MockTransport(handler),
        )
        result = await llm.complete([{"role": "user", "content": "你好"}])
        pieces = []
        async for delta in llm.stream([{"role": "user", "content": "你好"}]):
            pieces.append(delta)
        await llm.aclose()
        return result, pieces, llm

    result, pieces, llm = asyncio.run(scenario())
    assert result.content == "完整回答"
    assert result.total_tokens == 10
    assert result.upstream_model == "demo-model"
    assert "".join(pieces) == "你好"
    assert llm.chat_url == "http://example.test/v1/chat/completions"
    assert len(captured) == 2
    assert captured[0]["model"] == "demo-model"
    assert captured[1]["stream"] is True


def test_mock_agent_roles_are_distinct():
    async def scenario():
        llm = MockLLM()
        research = await llm.complete(
            [
                {"role": "system", "content": "你是 ResearchCrew 的「研究员 Agent」"},
                {"role": "user", "content": "用户任务：测试主题"},
            ]
        )
        writer = await llm.complete(
            [
                {"role": "system", "content": "你是 ResearchCrew 的「报告撰写 Agent」"},
                {"role": "user", "content": "用户任务：测试主题\n研究简报：内容"},
            ]
        )
        return research.content, writer.content

    research, writer = asyncio.run(scenario())
    assert research.startswith("## 研究简报")
    assert writer.startswith("# 测试主题：调研报告")
    assert len(writer) > len(research)


def test_local_knowledge_retrieval():
    kb = KnowledgeBase(PROJECT_ROOT / "data" / "knowledge")
    results = kb.search("FastAPI SSE 流式输出", limit=4)
    assert results
    assert any("fastapi_sse" in item.source for item in results)
    assert len([item for item in results if item.source == "fastapi_sse.md"]) <= 2
    context = KnowledgeBase.format_context(results)
    assert "SSE" in context
    assert "[1]" in context


def test_pdf_exporter_handles_chinese_tables_and_code(tmp_path):
    markdown = """# 测试报告

## 摘要

这是一段中文摘要，用来验证字体、换行和分页。

- 第一项
- 第二项

| 列 A | 列 B |
| --- | --- |
| 数据 1 | 数据 2 |

```python
print("hello")
```

> 这是一段引用。
"""
    exporter = PDFExporter()
    path = exporter.export(
        markdown,
        tmp_path / "report.pdf",
        title="测试报告",
        metadata={"model": "mock/demo", "report_id": "test-123"},
    )
    assert path.exists()
    assert path.stat().st_size > 1000
    doc = fitz.open(path)
    try:
        text = "\n".join(page.get_text() for page in doc)
        assert doc.page_count >= 1
        assert "测试报告" in text
        assert "摘要" in text
        assert "第一项" in text
        assert doc.metadata["title"] == "测试报告"
        assert "report_id=test-123" in doc.metadata["keywords"]
    finally:
        doc.close()


def test_report_store_roundtrip(tmp_path):
    store = ReportStore(tmp_path / "reports")
    record = store.save(
        markdown="# 标题\n\n正文",
        query="测试",
        model="mock/demo",
        provider="mock",
        mode="multi_agent",
        usage={"total_tokens": 12},
        sources=[],
        phases=[],
    )
    loaded = store.get(record.id)
    assert loaded.markdown == "# 标题\n\n正文"
    assert store.list()[0]["id"] == record.id
    assert loaded.public()["has_pdf"] is False

    with pytest.raises(BadRequestError):
        store.get("../../etc/passwd")
