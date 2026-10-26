"""报告持久化：每一次多智能体回答都落成一份可追溯的资产。

目录结构：
data/reports/<report_id>/
  report.md      可继续编辑的 Markdown
  report.json    标题、模型、usage、阶段、来源等元数据
  report.pdf     可选，导出后生成

为什么不直接塞数据库？
- 教学项目希望零外部服务即可运行；
- 文件天然适合 Markdown/PDF 这种“文档型”产物；
- 生产环境可以把 ReportStore 换成 PostgreSQL + 对象存储，接口不变。
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.errors import BadRequestError, NotFoundError

_REPORT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,80}$")


def title_from_markdown(markdown: str, fallback: str = "未命名报告") -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            return title[:120] or fallback
    return fallback[:120]


@dataclass
class ReportRecord:
    id: str
    title: str
    query: str
    created_at: str
    model: str
    provider: str
    mode: str
    usage: Dict[str, int] = field(default_factory=dict)
    sources: List[Dict[str, Any]] = field(default_factory=list)
    phases: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    markdown: str = ""
    pdf_filename: Optional[str] = None

    def public(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("markdown", None)
        data.pop("pdf_filename", None)
        data["has_pdf"] = bool(self.pdf_filename)
        return data


class ReportStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()

    def _report_dir(self, report_id: str) -> Path:
        if not _REPORT_ID_RE.fullmatch(report_id):
            raise BadRequestError("非法的 report_id", code="invalid_report_id", param="report_id")
        return self.root / report_id

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(content, encoding="utf-8")
        temp.replace(path)

    @staticmethod
    def _write_json(path: Path, data: Dict[str, Any]) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def save(
        self,
        *,
        markdown: str,
        query: str,
        model: str,
        provider: str,
        mode: str,
        usage: Dict[str, int],
        sources: Optional[List[Dict[str, Any]]] = None,
        phases: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        title: Optional[str] = None,
    ) -> ReportRecord:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            now = datetime.now(timezone.utc)
            report_id = now.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
            report_dir = self.root / report_id
            report_dir.mkdir(parents=True, exist_ok=False)
            final_title = (
                title or title_from_markdown(markdown, fallback=query[:80] or "未命名报告")
            ).strip()
            record = ReportRecord(
                id=report_id,
                title=final_title,
                query=query,
                created_at=now.isoformat(),
                model=model,
                provider=provider,
                mode=mode,
                usage=usage,
                sources=sources or [],
                phases=phases or [],
                metadata=metadata or {},
                markdown=markdown,
            )
            self._write_text(report_dir / "report.md", markdown)
            self._write_json(report_dir / "report.json", self._serialize(record))
            return record

    @staticmethod
    def _serialize(record: ReportRecord) -> Dict[str, Any]:
        data = asdict(record)
        data.pop("markdown", None)
        return data

    def get(self, report_id: str) -> ReportRecord:
        report_dir = self._report_dir(report_id)
        meta_path = report_dir / "report.json"
        md_path = report_dir / "report.md"
        if not meta_path.exists() or not md_path.exists():
            raise NotFoundError(f"报告不存在：{report_id}", code="report_not_found")
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise NotFoundError(f"报告元数据损坏：{report_id}", code="report_corrupted") from exc
        data["markdown"] = md_path.read_text(encoding="utf-8")
        return ReportRecord(**data)

    def list(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.root.exists():
            return []
        records: List[Dict[str, Any]] = []
        for path in self.root.iterdir():
            if not path.is_dir() or not _REPORT_ID_RE.fullmatch(path.name):
                continue
            try:
                data = json.loads((path / "report.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            data.pop("markdown", None)
            data["has_pdf"] = bool(data.get("pdf_filename"))
            data.pop("pdf_filename", None)
            # 列表接口保持轻量，不返回完整研究预览。
            metadata = data.get("metadata")
            if isinstance(metadata, dict):
                metadata.pop("research_preview", None)
            records.append(data)
        records.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return records[: max(1, min(limit, 100))]

    def markdown_path(self, report_id: str) -> Path:
        path = self._report_dir(report_id) / "report.md"
        if not path.exists():
            raise NotFoundError(f"报告不存在：{report_id}", code="report_not_found")
        return path

    def pdf_path(self, report_id: str) -> Path:
        report_dir = self._report_dir(report_id)
        path = report_dir / "report.pdf"
        if not path.exists():
            raise NotFoundError(f"PDF 尚未生成：{report_id}", code="pdf_not_found")
        return path

    def mark_pdf(self, report_id: str, filename: str = "report.pdf") -> ReportRecord:
        with self._lock:
            report_dir = self._report_dir(report_id)
            meta_path = report_dir / "report.json"
            if not meta_path.exists():
                raise NotFoundError(f"报告不存在：{report_id}", code="report_not_found")
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            data["pdf_filename"] = filename
            self._write_json(meta_path, data)
            data["markdown"] = (report_dir / "report.md").read_text(encoding="utf-8")
            return ReportRecord(**data)
