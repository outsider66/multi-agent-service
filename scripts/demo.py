"""命令行演示：不启动 HTTP 服务，直接调用 ChatService。

用法（在 multi-agent-service 目录）：
    python scripts/demo.py "调研多智能体协作的落地方式" --mode multi_agent --pdf

默认读取 .env；没有配置真实模型时使用 mock/demo，完全离线。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.llm.registry import LLMRegistry  # noqa: E402
from app.reports.store import ReportStore  # noqa: E402
from app.schemas import ChatCompletionRequest  # noqa: E402
from app.service import ChatService  # noqa: E402
from app.tools.knowledge import KnowledgeBase, SearchService, TavilySearch  # noqa: E402
from app.tools.pdf import PDFExporter  # noqa: E402


def build_service(settings: Settings) -> ChatService:
    registry = LLMRegistry(settings)
    reports = ReportStore(settings.reports_path)
    knowledge = KnowledgeBase(settings.knowledge_path)
    tavily = (
        TavilySearch(settings.tavily_api_key, max_results=settings.tavily_max_results)
        if settings.tavily_api_key.strip()
        else None
    )
    return ChatService(
        settings=settings,
        registry=registry,
        reports=reports,
        pdf=PDFExporter(),
        search=SearchService(knowledge, tavily=tavily),
    )


async def run(args: argparse.Namespace) -> int:
    settings = Settings()
    settings.reports_path.mkdir(parents=True, exist_ok=True)
    settings.knowledge_path.mkdir(parents=True, exist_ok=True)
    service = build_service(settings)
    request = ChatCompletionRequest(
        model=args.model or settings.default_model,
        messages=[{"role": "user", "content": args.question}],
        mode=args.mode,
        export_pdf=args.pdf,
        temperature=args.temperature,
    )

    print(f"model={request.model} mode={request.mode} pdf={request.export_pdf}\n")
    prepared = service.prepare(request)
    try:
        async for event in service.stream(prepared):
            if event.type == "phase" and event.phase:
                phase = event.phase
                elapsed = f" ({phase['elapsed_ms']}ms)" if phase.get("elapsed_ms") else ""
                print(f"\n[phase:{phase['stage']}] {phase['label']}{elapsed}")
                if phase.get("detail"):
                    print(f"  {phase['detail']}")
            elif event.type == "delta":
                print(event.delta, end="", flush=True)
            elif event.type == "done" and event.result:
                result = event.result
                print("\n\n" + "=" * 72)
                print(f"model={result.model} provider={result.provider} mode={result.mode}")
                print(f"usage={result.usage}")
                if result.report_id:
                    print(f"report_id={result.report_id}")
                    print(f"markdown={settings.reports_path / result.report_id / 'report.md'}")
                if result.pdf_url:
                    print(f"pdf={settings.reports_path / result.report_id / 'report.pdf'}")
                if result.sources:
                    print("sources=" + ", ".join(item.get("source", "") for item in result.sources))
    finally:
        await service.registry.aclose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ResearchCrew 离线 / 真实模型演示")
    parser.add_argument(
        "question",
        nargs="?",
        default="调研多智能体协作在企业知识管理中的落地方式，并给出三条建议",
    )
    parser.add_argument("--model", default=None, help="例如 mock/demo、ollama/qwen2.5:7b")
    parser.add_argument("--mode", choices=["single", "multi_agent"], default="multi_agent")
    parser.add_argument("--pdf", action="store_true", help="生成后自动导出 PDF")
    parser.add_argument("--temperature", type=float, default=0.3)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
