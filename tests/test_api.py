from __future__ import annotations

from tests.helpers import parse_sse, pdf_pages, stream_content


def test_health_and_models(client):
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["default_model"] == "mock/demo"

    models = client.get("/v1/models")
    assert models.status_code == 200
    data = models.json()
    assert data["object"] == "list"
    ids = {item["id"] for item in data["data"]}
    assert "mock/demo" in ids
    assert any(item.startswith("ollama/") for item in ids)


def test_single_completion_is_openai_compatible(client):
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "mock/demo",
            "messages": [{"role": "user", "content": "你好，介绍一下你自己"}],
            "mode": "single",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["id"].startswith("chatcmpl-")
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert "ResearchCrew" in body["choices"][0]["message"]["content"]
    assert body["usage"]["total_tokens"] > 0
    assert body["x_agent"]["mode"] == "single"
    assert body["x_agent"]["report_id"] is None


def test_multi_agent_report_markdown_pdf_and_list(client):
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "mock/demo",
            "messages": [
                {
                    "role": "user",
                    "content": "调研多智能体协作在企业知识管理中的落地方式，并给出三条建议",
                }
            ],
            "mode": "multi_agent",
            "export_pdf": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    content = body["choices"][0]["message"]["content"]
    assert content.startswith("# ")
    assert "## 一、摘要" in content
    assert "研究简报" not in content[:30]

    meta = body["x_agent"]
    assert meta["mode"] == "multi_agent"
    assert meta["report_id"]
    assert meta["pdf_url"] == f"/v1/reports/{meta['report_id']}/pdf"
    assert len(meta["phases"]) >= 3
    assert meta["sources"]

    report_id = meta["report_id"]
    report = client.get(f"/v1/reports/{report_id}")
    assert report.status_code == 200
    report_data = report.json()["data"]
    assert report_data["title"]
    assert report_data["has_pdf"] is True
    assert report_data["metadata"]["research_preview"].startswith("## 研究简报")

    markdown = client.get(f"/v1/reports/{report_id}/markdown")
    assert markdown.status_code == 200
    assert markdown.text.startswith("# ")

    pdf = client.get(f"/v1/reports/{report_id}/pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert pdf.content.startswith(b"%PDF-")
    assert len(pdf.content) < 3 * 1024 * 1024
    pages, text = pdf_pages(pdf.content)
    assert pages >= 1
    assert "摘要" in text

    listing = client.get("/v1/reports")
    assert listing.status_code == 200
    assert any(item["id"] == report_id for item in listing.json()["data"])


def test_streaming_sse_contains_phases_and_content(client):
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "mock/demo",
            "messages": [{"role": "user", "content": "调研 RAG 的引用校验方案"}],
            "mode": "multi_agent",
            "stream": True,
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    chunks, done, error = parse_sse(response.text)
    assert done is True
    assert error is None
    assert chunks
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"

    phases = [chunk["x_phase"] for chunk in chunks if "x_phase" in chunk]
    assert any(item["stage"] == "research" for item in phases)
    assert any(item["stage"] == "write" for item in phases)

    content = stream_content(chunks)
    assert content.startswith("# ")
    final = [chunk for chunk in chunks if chunk.get("x_agent")]
    assert final
    assert final[-1]["choices"][0]["finish_reason"] == "stop"
    assert final[-1]["x_agent"]["report_id"]
    assert final[-1]["usage"]["total_tokens"] > 0


def test_invalid_requests_return_openai_style_error(client):
    response = client.post("/v1/chat/completions", json={"messages": []})
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"

    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "assistant", "content": "只有 assistant 消息"}]},
    )
    assert response.status_code == 400
    assert "user" in response.json()["error"]["message"]


def test_auth_is_optional_but_enforced_when_configured(auth_client):
    assert auth_client.get("/healthz").status_code == 200
    denied = auth_client.get("/v1/models")
    assert denied.status_code == 401
    assert denied.headers.get("www-authenticate") == "Bearer"
    assert denied.json()["error"]["code"] == "invalid_api_key"

    allowed = auth_client.get("/v1/models", headers={"Authorization": "Bearer test-secret"})
    assert allowed.status_code == 200


def test_pdf_tool_endpoint(client):
    response = client.post(
        "/v1/tools/pdf",
        json={
            "title": "工具导出测试",
            "markdown": "# 工具导出测试\n\n## 摘要\n\n- 第一项\n- 第二项\n\n| 列 A | 列 B |\n| --- | --- |\n| 1 | 2 |\n",
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF-")
    pages, text = pdf_pages(response.content)
    assert pages >= 1
    assert "工具导出测试" in text
