from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.api.deps import require_api_key
from app.config import Settings
from app.errors import BadRequestError
from app.schemas import PDFToolRequest
from app.tools.pdf import PDFExporter

router = APIRouter(tags=["tools"], dependencies=[Depends(require_api_key)])


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "researchcrew-report"
    if not cleaned.lower().endswith(".pdf"):
        cleaned += ".pdf"
    return cleaned[:120]


@router.post("/v1/tools/pdf")
async def markdown_to_pdf(body: PDFToolRequest, request: Request):
    settings: Settings = request.app.state.settings
    pdf: PDFExporter = request.app.state.pdf
    if len(body.markdown) > max(settings.max_report_chars * 2, 60000):
        raise BadRequestError(
            "Markdown 内容过长，无法导出 PDF", code="markdown_too_long", param="markdown"
        )

    filename = _safe_filename(body.filename or "researchcrew-report.pdf")
    temp_dir = Path(settings.data_dir) / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"{uuid.uuid4().hex}-{filename}"
    try:
        await asyncio.to_thread(
            pdf.export,
            body.markdown,
            temp_path,
            title=body.title or "ResearchCrew 导出报告",
        )
        content = await asyncio.to_thread(temp_path.read_bytes)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)

    encoded = quote(filename)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{encoded}"
        },
    )
