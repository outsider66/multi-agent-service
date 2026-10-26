from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse

from app.api.deps import require_api_key
from app.config import Settings
from app.reports.store import ReportStore
from app.tools.pdf import PDFExporter

router = APIRouter(tags=["reports"], dependencies=[Depends(require_api_key)])


@router.get("/v1/reports")
async def list_reports(request: Request, limit: int = Query(default=20, ge=1, le=100)):
    store: ReportStore = request.app.state.reports
    return {"object": "list", "data": store.list(limit=limit)}


@router.get("/v1/reports/{report_id}")
async def get_report(report_id: str, request: Request):
    store: ReportStore = request.app.state.reports
    report = store.get(report_id)
    return {"object": "report", "data": report.public()}


@router.get("/v1/reports/{report_id}/markdown")
async def get_report_markdown(report_id: str, request: Request):
    store: ReportStore = request.app.state.reports
    path = store.markdown_path(report_id)
    return FileResponse(
        path,
        media_type="text/markdown; charset=utf-8",
        filename=f"{report_id}.md",
    )


@router.get("/v1/reports/{report_id}/pdf")
async def get_report_pdf(report_id: str, request: Request):
    store: ReportStore = request.app.state.reports
    settings: Settings = request.app.state.settings
    pdf: PDFExporter = request.app.state.pdf

    report = store.get(report_id)
    pdf_path = Path(settings.reports_path) / report.id / "report.pdf"
    if not pdf_path.exists():
        await asyncio.to_thread(
            pdf.export,
            report.markdown,
            pdf_path,
            title=report.title,
            metadata={"model": report.model, "report_id": report.id},
        )
        await asyncio.to_thread(store.mark_pdf, report.id)
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=f"{report.id}.pdf",
    )
