from __future__ import annotations

import json
import re

import fitz


def parse_sse(text: str):
    chunks = []
    done = False
    error = None
    for block in re.split(r"\n\n+", text.strip()):
        if not block.strip():
            continue
        event_name = "message"
        data_lines = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        data_text = "\n".join(data_lines)
        if not data_text:
            continue
        if data_text == "[DONE]":
            done = True
            continue
        payload = json.loads(data_text)
        if event_name == "error" or "error" in payload:
            error = payload.get("error", payload)
            continue
        chunks.append(payload)
    return chunks, done, error


def stream_content(chunks) -> str:
    parts = []
    for chunk in chunks:
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        if delta.get("content"):
            parts.append(delta["content"])
    return "".join(parts)


def pdf_pages(content: bytes):
    doc = fitz.open(stream=content, filetype="pdf")
    try:
        return doc.page_count, "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()
