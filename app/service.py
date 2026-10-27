"""应用服务层：把 Engine、模型、检索、报告存储和 PDF 串起来。

HTTP 路由只做协议转换，真正的业务流程在这里，便于单测和 CLI 复用。

一次请求的完整链路（详见 docs/LEARNING_GUIDE.md Day 2）：
    routes_chat.py
      → ChatService.prepare()        解析请求、选模型、选引擎
      → Engine.run()                 多 Agent 协作，产出 phase / delta / result 事件
      → ChatService._persist_report() 保存 Markdown/JSON，可选导出 PDF
      → ChatService.stream()          包装成下游可消费的 ChatEvent 流
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Literal, Optional

from app.config import Settings
from app.engine.base import BaseEngine, EngineResult
from app.engine.multi_agent import MultiAgentEngine
from app.engine.single import SingleAgentEngine
from app.errors import ProviderError
from app.llm.base import BaseLLM
from app.llm.registry import LLMRegistry, ModelRoute
from app.reports.store import ReportRecord, ReportStore
from app.schemas import ChatCompletionRequest
from app.tools.knowledge import SearchService
from app.tools.pdf import PDFExporter

logger = logging.getLogger(__name__)


@dataclass
class PreparedChat:
    """`prepare()` 的产物：一次请求在进入流式生成前已经确定好的全部上下文。

    这样拆分的好处是：
    - 校验、选模型、选引擎这类“可能失败”的动作都发生在返回 StreamingResponse 之前；
    - `stream()` 只依赖这个结构，不用再关心原始 HTTP 请求长什么样。
    """

    request: ChatCompletionRequest  # 原始请求（已通过 Pydantic 校验）
    query: str  # 最后一条 user 消息的纯文本，即本次任务
    history: List[Dict[str, str]]  # 最后一条 user 消息之前的历史，形如 [{"role": ..., "content": ...}]
    llm: BaseLLM  # 具体模型客户端（mock / openai / oneapi / ollama）
    route: ModelRoute  # 模型路由信息：provider + upstream_model，可生成 canonical_id
    engine: BaseEngine  # 编排策略：单 Agent / 多 Agent / CrewAI


@dataclass
class ChatResult:
    """一次对话的最终结果，也是 `done` 事件里携带的内容。

    注意：这里已经包含了报告元数据，路由层可以直接把它编码成
    最后一条带 `x_agent` 的 SSE 帧或非流式 JSON 响应。
    """

    content: str  # 最终正文（Markdown）
    model: str  # 规范化模型 ID，如 openai/gpt-4o-mini
    provider: str  # 供应方名称，如 openai
    mode: str  # single / multi_agent
    usage: Dict[str, int] = field(default_factory=dict)  # token 用量统计
    phases: List[Dict[str, Any]] = field(default_factory=list)  # 各阶段耗时与说明
    sources: List[Dict[str, Any]] = field(default_factory=list)  # 检索到的资料来源
    research_preview: str = ""  # 研究简报前 800 字符，便于前端预览
    metadata: Dict[str, Any] = field(default_factory=dict)  # 额外元数据（如 pdf_error）
    report: Optional[Dict[str, Any]] = None  # 报告公开信息（不含正文）
    report_id: Optional[str] = None  # 报告 ID，可用于后续查询/下载
    pdf_url: Optional[str] = None  # PDF 下载地址（仅导出成功时存在）
    draft_url: Optional[str] = None  # 草稿版 Markdown 地址（仅审核重写且保留草稿时存在）

    def agent_metadata(self) -> Dict[str, Any]:
        """汇总给客户端看的智能体元数据（对应 SSE 中的 `x_agent`）。"""
        return {
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model,
            "phases": self.phases,
            "sources": self.sources,
            "research_preview": self.research_preview,
            "report_id": self.report_id,
            "pdf_url": self.pdf_url,
            "draft_url": self.draft_url,
            **self.metadata,
        }


@dataclass
class ChatEvent:
    """服务层对外的细粒度事件，比 EngineEvent 多了一层 `done`。

    EngineEvent: phase / delta / revision / result  —— 引擎内部，result 是原始产物
    ChatEvent:   phase / delta / revision / done    —— 服务层，done 携带已持久化的 ChatResult
    """

    type: Literal["phase", "delta", "revision", "done"]
    phase: Optional[Dict[str, Any]] = None  # type == "phase" 时有效
    delta: str = ""  # type == "delta" 时有效：正文增量
    revision: Optional[Dict[str, Any]] = None  # type == "revision" 时有效：审核后的修改版
    result: Optional[ChatResult] = None  # type == "done" 时有效：最终结果


class ChatService:
    """业务流程总装线：请求准备 → 引擎执行 → 报告落盘 → 事件输出。

    依赖全部通过构造函数注入（而不是在内部 new），这样测试可以替换成
    假实现，也可以被 scripts/demo.py 直接复用。
    """

    def __init__(
        self,
        *,
        settings: Settings,
        registry: LLMRegistry,
        reports: ReportStore,
        pdf: PDFExporter,
        search: SearchService,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.reports = reports
        self.pdf = pdf
        self.search = search
        # 引擎是无状态的，可以复用：单 Agent 一份、多 Agent 一份（多 Agent 需要检索能力）
        self._single_engine = SingleAgentEngine()
        self._multi_engine = MultiAgentEngine(search)

    # ------------------------------------------------------------------
    # 1. 请求准备
    # ------------------------------------------------------------------
    def prepare(self, request: ChatCompletionRequest) -> PreparedChat:
        """在流开始之前完成所有可能失败的准备工作。

        顺序很重要：
        1. 解析模型 → 未配置 Key 会在这里抛 ProviderNotConfiguredError（可返回 4xx/5xx）；
        2. 提取 query / history；
        3. 选择编排引擎（single / crewai / 内置多 Agent）。
        """
        # registry.get() 内部会做“provider/模型”解析、缓存客户端，并在缺 Key 时报错
        llm, route = self.registry.get(request.model)

        # 本次任务 = 最后一条非空 user 消息；历史 = 它之前的所有消息
        query = request.last_user_message()
        history = [
            {"role": message.role, "content": message.text()}
            for message in request.history()
            if message.text().strip()  # 过滤空内容，避免把空消息喂给模型
        ]

        if request.mode == "single":
            # 单 Agent：一次模型调用，适合快速问答
            engine: BaseEngine = self._single_engine
        elif self.settings.orchestrator == "crewai":
            # CrewAI 属于可选依赖，按需惰性导入，未安装时不影响默认流程
            from app.engine.crewai_engine import CrewAIEngine

            engine = CrewAIEngine(self.settings, route, self.search)
        else:
            # 默认：内置多 Agent（研究 + 写作），零额外依赖
            engine = self._multi_engine

        return PreparedChat(
            request=request,
            query=query,
            history=history,
            llm=llm,
            route=route,
            engine=engine,
        )

    # ------------------------------------------------------------------
    # 2. 执行并输出事件流
    # ------------------------------------------------------------------
    async def stream(self, prepared: PreparedChat) -> AsyncIterator[ChatEvent]:
        """执行引擎，转发阶段/正文事件，最后完成报告持久化并产出 `done`。

        事件转换规则：
        - EngineEvent.phase    → ChatEvent.phase（stage/label/detail/agent/elapsed_ms）
        - EngineEvent.delta    → ChatEvent.delta（只转发非空增量）
        - EngineEvent.revision → ChatEvent.revision（审核后的修改版，独立通道）
        - EngineEvent.result   → 暂存，等引擎结束后统一持久化
        """
        result: Optional[EngineResult] = None
        async for event in prepared.engine.run(
            query=prepared.query,
            history=prepared.history,
            llm=prepared.llm,
            temperature=prepared.request.temperature,
            max_tokens=prepared.request.effective_max_tokens,  # max_completion_tokens 优先
        ):
            if event.type == "phase" and event.phase is not None:
                # Phase 是 dataclass，public() 转成可 JSON 序列化的 dict
                yield ChatEvent(type="phase", phase=event.phase.public())
            elif event.type == "delta" and event.delta:
                yield ChatEvent(type="delta", delta=event.delta)
            elif event.type == "revision" and event.revision is not None:
                yield ChatEvent(
                    type="revision",
                    revision={
                        "index": event.revision.index,
                        "reason": event.revision.reason[:300],
                        "markdown": event.revision.markdown,
                    },
                )
            elif event.type == "result" and event.result is not None:
                # 不立即下发，先拿去做持久化，避免“正文已推完但报告没存上”
                result = event.result

        if result is None:
            # 引擎没有产出 result 属于协议违约，按上游错误处理
            raise ProviderError("Agent 没有返回最终结果", code="empty_agent_result")

        # 只有多 Agent 或显式要求导出 PDF 时才落盘；否则报告相关字段为空
        report, pdf_url = await self._persist_report(
            prepared=prepared,
            result=result,
        )
        # 草稿只有在“审核重写且保留草稿”时才存在；前端据此显示下载入口
        draft_url = (
            f"/v1/reports/{report.id}/draft"
            if report is not None and report.draft_filename
            else None
        )
        mode = "multi_agent" if prepared.request.mode == "multi_agent" else "single"
        yield ChatEvent(
            type="done",
            result=ChatResult(
                content=result.content,
                model=prepared.route.canonical_id,  # 返回规范化 ID，而不是用户原始输入
                provider=prepared.route.provider,
                mode=mode,
                usage=result.usage,
                phases=result.phases,
                sources=result.sources,
                research_preview=result.research_preview,
                metadata=result.metadata,
                report=report.public() if report else None,  # public() 不含 Markdown 正文
                report_id=report.id if report else None,
                pdf_url=pdf_url,
                draft_url=draft_url,
            ),
        )

    async def complete(self, prepared: PreparedChat) -> ChatResult:
        """非流式入口：只是把 `stream()` 消费完并取最后一个结果。

        这样流式与非流式共用同一套业务逻辑，不会出现两条逻辑逐渐走偏的问题。
        """
        final: Optional[ChatResult] = None
        async for event in self.stream(prepared):
            if event.type == "done" and event.result is not None:
                final = event.result
        if final is None:
            raise ProviderError("Agent 没有返回最终结果", code="empty_agent_result")
        return final

    # ------------------------------------------------------------------
    # 3. 报告持久化
    # ------------------------------------------------------------------
    async def _persist_report(
        self, *, prepared: PreparedChat, result: EngineResult
    ) -> tuple[Optional[ReportRecord], Optional[str]]:
        """保存报告资产，返回 (报告记录, PDF 地址)；不需要保存时返回 (None, None)。

        触发条件：mode == multi_agent 或 export_pdf == True。
        单 Agent 且不导出 PDF 的普通问答不落盘，避免产生大量无价值文件。
        """
        should_save = prepared.request.mode == "multi_agent" or prepared.request.export_pdf
        if not should_save:
            return None, None

        # 元数据 = 引擎元数据 + 调用方自定义 metadata + 研究简报预览
        metadata = dict(result.metadata)
        if prepared.request.metadata:
            metadata["request_metadata"] = prepared.request.metadata
        metadata["research_preview"] = result.research_preview

        # 草稿和最终版内容一致（审核通过）时没有保存价值，避免磁盘翻倍
        include_draft = (
            prepared.request.report_versions == "both"
            and bool(result.draft_content.strip())
            and result.draft_content != result.content
        )
        metadata["draft_saved"] = include_draft

        # 报告 ID 在这里生成（时间戳 + 随机串），磁盘 IO 放进线程池避免阻塞事件循环
        record = await asyncio.to_thread(
            self.reports.save,
            markdown=result.content,
            draft_markdown=result.draft_content if include_draft else None,
            query=prepared.query,
            model=prepared.route.canonical_id,
            provider=prepared.route.provider,
            mode=prepared.request.mode,
            usage=result.usage,
            sources=result.sources,
            phases=result.phases,
            metadata=metadata,
        )
        pdf_url: Optional[str] = None
        if prepared.request.export_pdf:
            # 路径固定为 <reports_path>/<report_id>/report.pdf
            pdf_path = self.settings.reports_path / record.id / "report.pdf"
            try:
                await asyncio.to_thread(
                    self.pdf.export,
                    result.content,
                    pdf_path,
                    title=record.title,
                    metadata={"model": record.model, "report_id": record.id},
                )
                # 在 report.json 里标记 has_pdf，前端据此显示下载按钮
                record = await asyncio.to_thread(self.reports.mark_pdf, record.id)
                pdf_url = f"/v1/reports/{record.id}/pdf"
            except Exception as exc:
                # PDF 失败不回滚已经保存的 Markdown，用户可以稍后重试导出。
                logger.exception("报告 PDF 导出失败 report_id=%s: %s", record.id, exc)
                metadata["pdf_error"] = str(exc)
        return record, pdf_url
