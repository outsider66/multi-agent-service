"""零外部文档服务的 Markdown → PDF 导出器（基于 PyMuPDF）。

为什么自己排版而不是调用 Word / LibreOffice？
- 服务端不需要安装桌面软件，Docker 里也能跑；
- 生成逻辑可控，方便在面试中讲清楚“分页、换行、字体、表格”；
- 同时输出 Markdown 和 PDF，前者可编辑，后者可交付。

实现范围：标题、段落、列表、引用、代码块、简单表格、分隔线、页眉页脚、页码。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

try:  # PyMuPDF 新版本推荐 import pymupdf
    import pymupdf as fitz  # type: ignore
except ImportError:  # pragma: no cover - 旧版本兼容
    import fitz  # type: ignore

from app.errors import ServiceError

PAGE_WIDTH = 595.28  # A4
PAGE_HEIGHT = 841.89
MARGIN = 54.0
CONTENT_WIDTH = PAGE_WIDTH - MARGIN * 2
BOTTOM = 62.0
TOP = 64.0

NAVY = (0.09, 0.19, 0.32)
BLUE = (0.12, 0.35, 0.62)
GRAY = (0.38, 0.42, 0.48)
LIGHT_GRAY = (0.55, 0.58, 0.62)
BLACK = (0.10, 0.11, 0.13)
CODE_BG = (0.95, 0.96, 0.975)
TABLE_HEADER_BG = (0.92, 0.95, 0.985)
TABLE_BORDER = (0.78, 0.81, 0.86)
QUOTE_BAR = (0.55, 0.68, 0.86)


@dataclass
class _FontSpec:
    name: str
    path: Optional[str]
    font: "fitz.Font"


def _load_font() -> _FontSpec:
    """优先使用系统中文比例字体；找不到时使用 PyMuPDF 内置 CJK 字体。

    内置字体名为 china-s（Droid Sans Fallback），通过 fontbuffer 注册后，
    ASCII 不会出现全角间距，适合作为跨平台兜底。
    """
    candidates = [
        ("Microsoft YaHei", "C:/Windows/Fonts/msyh.ttc"),
        ("SimHei", "C:/Windows/Fonts/simhei.ttf"),
        ("PingFang SC", "/System/Library/Fonts/PingFang.ttc"),
        ("Noto Sans CJK", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        ("Noto Sans CJK", "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf"),
        ("WenQuanYi", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    ]
    for _name, path in candidates:
        if path and Path(path).exists():
            try:
                return _FontSpec(name="RCBody", path=path, font=fitz.Font(fontfile=path))
            except Exception:
                continue
    return _FontSpec(name="RCBody", path=None, font=fitz.Font(fontname="china-s"))


def _strip_inline(text: str) -> str:
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1（\2）", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    text = text.replace("~~", "")
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _is_table_separator(line: str) -> bool:
    stripped = line.strip()
    if "-" not in stripped:
        return False
    return bool(re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?", stripped))


class PDFExporter:
    def __init__(self) -> None:
        self._font = _load_font()

    # ---------- 基础绘制 ----------

    def _register_font(self, page: "fitz.Page") -> None:
        if self._font.path:
            page.insert_font(fontname=self._font.name, fontfile=self._font.path)
        else:
            page.insert_font(fontname=self._font.name, fontbuffer=self._font.font.buffer)

    def _new_page(self) -> None:
        self.page = self.doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        self._register_font(self.page)
        self.page_no += 1
        self.y = TOP

    def _width(self, text: str, size: float) -> float:
        try:
            return float(self._font.font.text_length(text, fontsize=size))
        except Exception:
            # 极少数缺字形场景下的近似兜底
            return sum(size * (1.0 if ord(char) > 0x2E7F else 0.55) for char in text)

    def _wrap(self, text: str, size: float, width: float) -> List[str]:
        text = re.sub(r"\s+", " ", _strip_inline(text)).strip()
        if not text:
            return [""]
        tokens = re.findall(r"[A-Za-z0-9_\-./:%@+]+|\s+|.", text)
        lines: List[str] = []
        current = ""
        for token in tokens:
            if token.isspace():
                if current:
                    current += " "
                continue
            candidate = current + token
            if current and self._width(candidate, size) > width:
                lines.append(current.rstrip())
                current = token.lstrip()
            else:
                current = candidate
            # 处理超长英文单词 / URL：按字符切
            while self._width(current, size) > width and len(current) > 1:
                cut = len(current) - 1
                while cut > 1 and self._width(current[:cut], size) > width:
                    cut -= 1
                lines.append(current[:cut])
                current = current[cut:]
        if current.strip():
            lines.append(current.rstrip())
        return lines or [""]

    def _ensure(self, height: float) -> None:
        if self.y + height > PAGE_HEIGHT - BOTTOM:
            self._new_page()

    def _draw_line(
        self,
        text: str,
        *,
        size: float,
        color: Tuple[float, float, float],
        indent: float = 0.0,
        leading: Optional[float] = None,
    ) -> None:
        line_height = leading or size * 1.62
        self._ensure(line_height)
        self.page.insert_text(
            (MARGIN + indent, self.y + size),
            text,
            fontname=self._font.name,
            fontsize=size,
            color=color,
        )
        self.y += line_height

    # ---------- 块级元素 ----------

    def heading(self, text: str, level: int) -> None:
        text = _strip_inline(text)
        if not text:
            return
        if level == 1:
            size, color, before, after = 20.0, NAVY, 10.0, 6.0
        elif level == 2:
            size, color, before, after = 14.0, BLUE, 15.0, 4.0
        elif level == 3:
            size, color, before, after = 11.5, NAVY, 11.0, 3.0
        else:
            size, color, before, after = 10.5, BLACK, 8.0, 2.0
        self.y += before
        # 避免标题落在页面最底部，和正文被分页拆开（孤行标题）。
        follow_lines = 3 if level <= 2 else 2
        if self.y + size * 1.55 + follow_lines * size * 1.72 > PAGE_HEIGHT - BOTTOM:
            self._new_page()
        lines = self._wrap(text, size, CONTENT_WIDTH)
        for index, line in enumerate(lines):
            self._ensure(size * 1.55)
            self._draw_line(line, size=size, color=color, leading=size * 1.55)
        if level == 1:
            self._ensure(8)
            self.page.draw_line(
                (MARGIN, self.y + 1),
                (MARGIN + CONTENT_WIDTH, self.y + 1),
                color=(0.72, 0.78, 0.86),
                width=0.8,
            )
            self.y += 7
        self.y += after

    def paragraph(
        self, text: str, *, size: float = 10.5, color: Tuple[float, float, float] = BLACK
    ) -> None:
        lines = self._wrap(text, size, CONTENT_WIDTH)
        leading = size * 1.72
        for line in lines:
            self._draw_line(line, size=size, color=color, leading=leading)
        self.y += 3

    def bullet(self, text: str, *, level: int = 0, marker: str = "•") -> None:
        indent = 10.0 + level * 16.0
        text_indent = indent + 15.0
        lines = self._wrap(text, 10.5, CONTENT_WIDTH - text_indent)
        leading = 10.5 * 1.65
        for index, line in enumerate(lines):
            self._ensure(leading)
            if index == 0:
                self.page.insert_text(
                    (MARGIN + indent, self.y + 10.5),
                    marker,
                    fontname=self._font.name,
                    fontsize=10.5,
                    color=BLUE,
                )
            self.page.insert_text(
                (MARGIN + text_indent, self.y + 10.5),
                line,
                fontname=self._font.name,
                fontsize=10.5,
                color=BLACK,
            )
            self.y += leading
        self.y += 2

    def quote(self, lines: Sequence[str]) -> None:
        text = " ".join(_strip_inline(line) for line in lines if line.strip())
        if not text:
            return
        wrapped = self._wrap(text, 10.2, CONTENT_WIDTH - 24)
        leading = 10.2 * 1.7
        start_y = self.y
        for line in wrapped:
            self._ensure(leading)
            self.page.insert_text(
                (MARGIN + 18, self.y + 10.2),
                line,
                fontname=self._font.name,
                fontsize=10.2,
                color=GRAY,
            )
            self.y += leading
        self.page.draw_line(
            (MARGIN + 5, start_y + 1),
            (MARGIN + 5, self.y - 4),
            color=QUOTE_BAR,
            width=2.2,
        )
        self.y += 4

    def code_block(self, lines: Sequence[str]) -> None:
        size = 8.6
        leading = 12.2
        for raw in lines:
            safe = raw.replace("\t", "    ")
            for line in self._wrap_code(safe, size, CONTENT_WIDTH - 20):
                self._ensure(leading)
                self.page.draw_rect(
                    fitz.Rect(
                        MARGIN + 4, self.y - 1, MARGIN + CONTENT_WIDTH - 4, self.y + leading - 1
                    ),
                    color=None,
                    fill=CODE_BG,
                )
                self.page.insert_text(
                    (MARGIN + 10, self.y + size),
                    line,
                    fontname=self._font.name,
                    fontsize=size,
                    color=(0.18, 0.22, 0.28),
                )
                self.y += leading
        self.y += 5

    def _wrap_code(self, line: str, size: float, width: float) -> List[str]:
        if not line:
            return [""]
        pieces: List[str] = []
        current = ""
        for char in line:
            if self._width(current + char, size) > width and current:
                pieces.append(current)
                current = char
            else:
                current += char
        if current:
            pieces.append(current)
        return pieces or [""]

    def table(self, rows: Sequence[Sequence[str]]) -> None:
        if not rows:
            return
        column_count = max(len(row) for row in rows)
        if column_count == 0:
            return
        rows = [list(row) + [""] * (column_count - len(row)) for row in rows]
        first_width = CONTENT_WIDTH * (0.24 if column_count > 2 else 0.34)
        other_width = (CONTENT_WIDTH - first_width) / max(1, column_count - 1)
        widths = [first_width] + [other_width] * (column_count - 1)
        font_size = 8.9
        leading = 11.6
        padding = 5.0

        # 第一行视为表头
        header = rows[0]
        body = rows[1:]
        self._draw_table_header(header, widths, font_size, leading, padding)
        for row in body:
            self._draw_table_row(row, widths, font_size, leading, padding, header)
        self.y += 5

    def _cell_lines(self, text: str, width: float, size: float) -> List[str]:
        return self._wrap(text, size, max(12.0, width - 10.0))

    def _row_height(
        self,
        row: Sequence[str],
        widths: Sequence[float],
        size: float,
        leading: float,
        padding: float,
    ) -> float:
        counts = [len(self._cell_lines(cell, width, size)) for cell, width in zip(row, widths)]
        return max(counts) * leading + padding * 2

    def _draw_table_header(
        self,
        row: Sequence[str],
        widths: Sequence[float],
        size: float,
        leading: float,
        padding: float,
    ) -> None:
        height = self._row_height(row, widths, size, leading, padding)
        self._ensure(height + 4)
        x = MARGIN
        self.page.draw_rect(
            fitz.Rect(x, self.y, x + CONTENT_WIDTH, self.y + height),
            color=None,
            fill=TABLE_HEADER_BG,
        )
        for cell, width in zip(row, widths):
            lines = self._cell_lines(cell, width, size)
            for index, line in enumerate(lines):
                self.page.insert_text(
                    (x + padding, self.y + padding + size + index * leading),
                    line,
                    fontname=self._font.name,
                    fontsize=size,
                    color=NAVY,
                )
            self.page.draw_line((x, self.y), (x, self.y + height), color=TABLE_BORDER, width=0.5)
            x += width
        self.page.draw_line(
            (MARGIN + CONTENT_WIDTH, self.y),
            (MARGIN + CONTENT_WIDTH, self.y + height),
            color=TABLE_BORDER,
            width=0.5,
        )
        self.page.draw_line(
            (MARGIN, self.y + height),
            (MARGIN + CONTENT_WIDTH, self.y + height),
            color=TABLE_BORDER,
            width=0.5,
        )
        self.y += height

    def _draw_table_row(
        self,
        row: Sequence[str],
        widths: Sequence[float],
        size: float,
        leading: float,
        padding: float,
        header: Sequence[str],
    ) -> None:
        height = self._row_height(row, widths, size, leading, padding)
        if self.y + height > PAGE_HEIGHT - BOTTOM:
            self._new_page()
            self._draw_table_header(header, widths, size, leading, padding)
        x = MARGIN
        for cell, width in zip(row, widths):
            lines = self._cell_lines(cell, width, size)
            for index, line in enumerate(lines):
                self.page.insert_text(
                    (x + padding, self.y + padding + size + index * leading),
                    line,
                    fontname=self._font.name,
                    fontsize=size,
                    color=BLACK,
                )
            self.page.draw_line((x, self.y), (x, self.y + height), color=TABLE_BORDER, width=0.5)
            x += width
        self.page.draw_line(
            (MARGIN + CONTENT_WIDTH, self.y),
            (MARGIN + CONTENT_WIDTH, self.y + height),
            color=TABLE_BORDER,
            width=0.5,
        )
        self.page.draw_line(
            (MARGIN, self.y + height),
            (MARGIN + CONTENT_WIDTH, self.y + height),
            color=TABLE_BORDER,
            width=0.5,
        )
        self.y += height

    def hr(self) -> None:
        self._ensure(15)
        self.y += 5
        self.page.draw_line(
            (MARGIN, self.y), (MARGIN + CONTENT_WIDTH, self.y), color=(0.82, 0.85, 0.89), width=0.7
        )
        self.y += 10

    # ---------- Markdown 解析 ----------

    def render(self, markdown: str, *, title: Optional[str] = None) -> None:
        lines = markdown.replace("\r\n", "\n").split("\n")
        # 如果正文没有 H1，用传入标题补一个
        if title and not any(line.startswith("# ") for line in lines[:5]):
            self.heading(title, 1)

        index = 0
        while index < len(lines):
            line = lines[index]
            stripped = line.strip()

            if not stripped:
                self.y += 4
                index += 1
                continue

            # 代码块
            fence = re.match(r"^\s*```", line)
            if fence:
                index += 1
                code_lines: List[str] = []
                while index < len(lines) and not re.match(r"^\s*```", lines[index]):
                    code_lines.append(lines[index])
                    index += 1
                index += 1
                self.code_block(code_lines)
                continue

            # 标题
            heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
            if heading:
                self.heading(heading.group(2), len(heading.group(1)))
                index += 1
                continue

            # 表格
            if (
                stripped.startswith("|")
                and index + 1 < len(lines)
                and _is_table_separator(lines[index + 1])
            ):
                rows: List[List[str]] = []
                while index < len(lines) and lines[index].strip().startswith("|"):
                    if not _is_table_separator(lines[index]):
                        raw_cells = lines[index].strip().strip("|").split("|")
                        rows.append([_strip_inline(cell) for cell in raw_cells])
                    index += 1
                self.table(rows)
                continue

            # 分隔线
            if re.fullmatch(r"\s*([-*_])\1{2,}\s*", stripped):
                self.hr()
                index += 1
                continue

            # 引用
            if stripped.startswith(">"):
                quote_lines: List[str] = []
                while index < len(lines) and lines[index].strip().startswith(">"):
                    quote_lines.append(re.sub(r"^\s*>\s?", "", lines[index]))
                    index += 1
                self.quote(quote_lines)
                continue

            # 无序列表
            bullet = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
            if bullet:
                level = min(3, len(bullet.group(1)) // 2)
                self.bullet(bullet.group(2), level=level)
                index += 1
                continue

            # 有序列表
            numbered = re.match(r"^(\s*)(\d+)[.)]\s+(.*)$", line)
            if numbered:
                level = min(3, len(numbered.group(1)) // 2)
                self.bullet(numbered.group(3), level=level, marker=f"{numbered.group(2)}.")
                index += 1
                continue

            # 普通段落：收集到空行 / 其他块级元素为止
            paragraph_lines = [stripped]
            index += 1
            while index < len(lines):
                nxt = lines[index]
                nxt_stripped = nxt.strip()
                if not nxt_stripped:
                    break
                if re.match(r"^(#{1,6})\s+", nxt_stripped):
                    break
                if nxt_stripped.startswith("```") or nxt_stripped.startswith(">"):
                    break
                if re.match(r"^\s*[-*+]\s+", nxt) or re.match(r"^\s*\d+[.)]\s+", nxt):
                    break
                if (
                    nxt_stripped.startswith("|")
                    and index + 1 < len(lines)
                    and _is_table_separator(lines[index + 1])
                ):
                    break
                paragraph_lines.append(nxt_stripped)
                index += 1
            self.paragraph(" ".join(paragraph_lines))

    # ---------- 输出 ----------

    def export(
        self,
        markdown: str,
        output_path: Path,
        *,
        title: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = output_path.with_suffix(".tmp.pdf")
        self.doc = fitz.open()
        self.page_no = 0
        self.y = TOP
        self._new_page()
        try:
            self.render(markdown, title=title)
            total_pages = self.doc.page_count
            for page_index in range(total_pages):
                page = self.doc[page_index]
                page.insert_text(
                    (MARGIN, 31),
                    "ResearchCrew · 多智能体研究报告",
                    fontname=self._font.name,
                    fontsize=8,
                    color=LIGHT_GRAY,
                )
                page.draw_line(
                    (MARGIN, 39), (PAGE_WIDTH - MARGIN, 39), color=(0.88, 0.90, 0.92), width=0.5
                )
                page.draw_line(
                    (MARGIN, PAGE_HEIGHT - 42),
                    (PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 42),
                    color=(0.88, 0.90, 0.92),
                    width=0.5,
                )
                page.insert_text(
                    (MARGIN, PAGE_HEIGHT - 28),
                    "ResearchCrew · OpenAI-compatible multi-agent service",
                    fontname=self._font.name,
                    fontsize=7.5,
                    color=LIGHT_GRAY,
                )
                page.insert_text(
                    (PAGE_WIDTH - MARGIN - 72, PAGE_HEIGHT - 28),
                    f"第 {page_index + 1} 页 / 共 {total_pages} 页",
                    fontname=self._font.name,
                    fontsize=8,
                    color=LIGHT_GRAY,
                )
            meta = {
                "title": title or "ResearchCrew 报告",
                "author": "ResearchCrew",
                "subject": "多智能体协作生成的调研报告",
                "creator": "ResearchCrew / PyMuPDF",
            }
            if metadata:
                # PyMuPDF 只接受固定 metadata 字段；业务字段放入 keywords，避免 set_metadata 报错。
                allowed = {
                    "title",
                    "author",
                    "subject",
                    "keywords",
                    "creator",
                    "producer",
                    "creationDate",
                    "modDate",
                    "trapped",
                }
                extras = []
                for key, value in metadata.items():
                    key = str(key)
                    value = str(value)
                    if key in allowed:
                        meta[key] = value
                    else:
                        extras.append(f"{key}={value}")
                if extras:
                    meta["keywords"] = (meta.get("keywords", "") + "；" + "；".join(extras)).strip(
                        "；"
                    )
            self.doc.set_metadata(meta)
            # 中文字体文件通常有十几 MB。只保留实际用到的字形，避免 PDF 体积失控。
            try:
                self.doc.subset_fonts()
            except Exception:
                pass
            self.doc.save(str(temp_path), garbage=4, deflate=True)
            self.doc.close()
            temp_path.replace(output_path)
            return output_path
        except Exception as exc:
            try:
                self.doc.close()
            except Exception:
                pass
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            if isinstance(exc, ServiceError):
                raise
            raise ServiceError(
                f"PDF 导出失败：{exc}", status_code=500, code="pdf_export_failed"
            ) from exc
