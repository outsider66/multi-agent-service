"""检索层：本地知识库 + 可选 Tavily 联网搜索。

默认只查本地 Markdown，因此完全离线；配置 TAVILY_API_KEY 后会先查网页，
再把本地资料合并进来。注意：检索结果始终是“数据”，不是指令，不能覆盖系统提示词。
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

_CJK_RE = re.compile(r"[\u3400-\u9fff]+")
_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")


@dataclass
class SearchResult:
    source: str
    title: str
    snippet: str
    score: float = 0.0
    url: Optional[str] = None

    def as_context_line(self, index: int) -> str:
        where = self.url or self.source
        return f"[{index}] {self.title}（{where}）\n{self.snippet}"


@dataclass
class _Chunk:
    source: str
    title: str
    text: str
    tokens: List[str] = field(default_factory=list)


def tokenize(text: str) -> List[str]:
    """中文用字符 bigram，英文用单词；无需 jieba 等分词依赖。"""
    tokens = [match.group(0).lower() for match in _WORD_RE.finditer(text)]
    for sequence in _CJK_RE.findall(text):
        if len(sequence) == 1:
            tokens.append(sequence)
        else:
            tokens.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return tokens


class KnowledgeBase:
    def __init__(self, root: Path, *, max_chunk_chars: int = 700) -> None:
        self.root = Path(root)
        self.max_chunk_chars = max_chunk_chars

    def _load_chunks(self) -> List[_Chunk]:
        chunks: List[_Chunk] = []
        if not self.root.exists():
            return chunks
        for path in sorted(self.root.glob("**/*")):
            if path.suffix.lower() not in {".md", ".txt"} or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("读取知识库文件失败 %s: %s", path, exc)
                continue
            title = path.stem
            for line in text.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            for block in re.split(r"\n\s*\n", text):
                block = block.strip()
                if not block or block.startswith("```"):
                    continue
                # 去掉 Markdown 标题符号，保留正文内容
                block = re.sub(r"^#{1,6}\s*", "", block, flags=re.MULTILINE).strip()
                if len(block) <= self.max_chunk_chars:
                    pieces = [block]
                else:
                    pieces = [
                        block[index : index + self.max_chunk_chars]
                        for index in range(0, len(block), self.max_chunk_chars)
                    ]
                for piece in pieces:
                    chunks.append(
                        _Chunk(
                            source=path.name,
                            title=title,
                            text=piece,
                            tokens=tokenize(title + "\n" + piece),
                        )
                    )
        return chunks

    def search(self, query: str, limit: int = 4) -> List[SearchResult]:
        chunks = self._load_chunks()
        if not chunks:
            return []
        query_tokens = list(dict.fromkeys(tokenize(query)))
        if not query_tokens:
            return []

        document_frequency: Counter = Counter()
        for chunk in chunks:
            document_frequency.update(set(chunk.tokens))
        total = len(chunks)

        scored: List[Tuple[float, _Chunk]] = []
        for chunk in chunks:
            counts = Counter(chunk.tokens)
            score = 0.0
            for token in query_tokens:
                tf = counts.get(token, 0)
                if not tf:
                    continue
                idf = math.log((total + 1) / (document_frequency[token] + 1)) + 1.0
                score += (1.0 + math.log(tf)) * idf
            if score > 0:
                score /= math.sqrt(max(1, len(chunk.tokens)))
                scored.append((score, chunk))

        scored.sort(key=lambda item: item[0], reverse=True)
        results: List[SearchResult] = []
        seen_text = set()
        per_source: Counter = Counter()
        for score, chunk in scored:
            fingerprint = chunk.text[:80]
            if fingerprint in seen_text:
                continue
            # 同一个文档最多取 2 段，避免 4 条结果全部来自同一篇文章。
            if per_source[chunk.source] >= 2:
                continue
            seen_text.add(fingerprint)
            per_source[chunk.source] += 1
            results.append(
                SearchResult(
                    source=chunk.source,
                    title=chunk.title,
                    snippet=chunk.text[: self.max_chunk_chars].strip(),
                    score=round(score, 4),
                )
            )
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def format_context(results: Iterable[SearchResult]) -> str:
        items = list(results)
        if not items:
            return "（没有检索到本地资料。请基于通用知识回答，并明确标注不确定性。）"
        return "\n\n".join(item.as_context_line(index + 1) for index, item in enumerate(items))


class TavilySearch:
    """可选的网页检索工具。没有 API Key 时不会实例化。"""

    endpoint = "https://api.tavily.com/search"

    def __init__(self, api_key: str, max_results: int = 4, timeout_seconds: float = 20.0) -> None:
        self.api_key = api_key
        self.max_results = max_results
        self.timeout_seconds = timeout_seconds

    async def search(self, query: str, limit: Optional[int] = None) -> List[SearchResult]:
        count = min(limit or self.max_results, self.max_results)
        payload = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": count,
            "include_answer": False,
            "include_raw_content": False,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(self.endpoint, json=payload)
            response.raise_for_status()
            data = response.json()
        results: List[SearchResult] = []
        for item in data.get("results", [])[:count]:
            results.append(
                SearchResult(
                    source=item.get("url") or "tavily",
                    title=item.get("title") or "网页资料",
                    snippet=(item.get("content") or "").strip()[:1200],
                    score=float(item.get("score") or 0.0),
                    url=item.get("url"),
                )
            )
        return results


class SearchService:
    def __init__(self, knowledge: KnowledgeBase, tavily: Optional[TavilySearch] = None) -> None:
        self.knowledge = knowledge
        self.tavily = tavily

    async def search(self, query: str, limit: int = 4) -> List[SearchResult]:
        local_results = self.knowledge.search(query, limit=limit)
        if not self.tavily:
            return local_results

        web_results: List[SearchResult] = []
        try:
            web_results = await self.tavily.search(query, limit=limit)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            # 联网失败不能阻断主流程；降级到本地知识库。
            logger.warning("Tavily 检索失败，降级到本地知识库：%s", exc)

        combined = web_results + local_results
        deduped: List[SearchResult] = []
        seen = set()
        for item in combined:
            key = item.url or (item.source, item.snippet[:80])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= limit:
                break
        return deduped
