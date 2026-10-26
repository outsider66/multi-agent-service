"""OpenAI Chat Completions 协议的通用 HTTP 客户端。

同一份代码可以连接：
- OpenAI 官方：https://api.openai.com/v1
- OneAPI / New API 等代理：http://localhost:3000/v1
- Ollama 的 OpenAI 兼容层：http://localhost:11434/v1
- vLLM / LM Studio / DashScope 兼容模式等

这就是“多模型适配层”的最小实现：差异被收敛到 base_url、api_key 和 model 三个参数。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from app.llm.base import (
    BaseLLM,
    LLMError,
    LLMResult,
    content_to_text,
    estimate_messages_tokens,
    estimate_tokens,
)

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


def _chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


class OpenAICompatibleLLM(BaseLLM):
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        base_url: str,
        api_key: str = "",
        timeout_seconds: float = 120.0,
        max_retries: int = 1,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.provider = provider
        self.upstream_model = model
        self.base_url = base_url.rstrip("/")
        self.chat_url = _chat_completions_url(base_url)
        self.max_retries = max_retries
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if extra_headers:
            headers.update(extra_headers)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=min(15.0, timeout_seconds)),
            headers=headers,
            transport=transport,
        )

    def _payload(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: Optional[int],
        *,
        stream: bool,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.upstream_model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        return payload

    async def complete(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: Optional[int] = None,
    ) -> LLMResult:
        response = await self._post_with_retry(
            self._payload(messages, temperature, max_tokens, stream=False)
        )
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError("上游返回的不是合法 JSON") from exc

        choices = data.get("choices") or []
        if not choices:
            raise LLMError("上游响应缺少 choices")
        message = choices[0].get("message") or {}
        text = content_to_text(message.get("content"))
        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or estimate_messages_tokens(messages))
        completion_tokens = int(usage.get("completion_tokens") or estimate_tokens(text))
        total_tokens = int(usage.get("total_tokens") or prompt_tokens + completion_tokens)
        return LLMResult(
            content=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finish_reason=choices[0].get("finish_reason") or "stop",
            upstream_model=data.get("model") or self.upstream_model,
        )

    async def stream(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        payload = self._payload(messages, temperature, max_tokens, stream=True)
        try:
            async with self._client.stream("POST", self.chat_url, json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    raise self._http_error(response.status_code, body)
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith(":"):
                        # SSE 注释 / keep-alive
                        continue
                    if line.startswith("data:"):
                        data_line = line[5:].strip()
                    else:
                        data_line = line.strip()
                    if not data_line:
                        continue
                    if data_line == "[DONE]":
                        break
                    try:
                        event = json.loads(data_line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("error"):
                        raise LLMError(self._format_error(event["error"]))
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = content_to_text(delta.get("content"))
                    if text:
                        yield text
        except httpx.RequestError as exc:
            raise LLMError(
                f"无法连接 {self.provider}（{self.chat_url}）：{exc}",
                status_code=503,
                code="provider_unreachable",
            ) from exc

    async def _post_with_retry(self, payload: Dict[str, Any]) -> httpx.Response:
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.post(self.chat_url, json=payload)
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(0.4 * (2**attempt))
                    continue
                raise LLMError(
                    f"无法连接 {self.provider}（{self.chat_url}）：{exc}",
                    status_code=503,
                    code="provider_unreachable",
                ) from exc

            if response.status_code < 400:
                return response

            if response.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                await asyncio.sleep(0.4 * (2**attempt))
                continue
            raise self._http_error(response.status_code, response.text)

        raise LLMError(f"调用 {self.provider} 失败：{last_error}")

    def _http_error(self, status_code: int, body: str) -> LLMError:
        message = f"{self.provider} 返回 HTTP {status_code}"
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict) and parsed.get("error"):
                message += "：" + self._format_error(parsed["error"])
        except (ValueError, TypeError):
            snippet = body.strip().replace("\n", " ")[:300]
            if snippet:
                message += f"：{snippet}"
        return LLMError(message, status_code=502, code="provider_http_error")

    @staticmethod
    def _format_error(error: Any) -> str:
        if isinstance(error, dict):
            return str(error.get("message") or error)
        return str(error)

    async def aclose(self) -> None:
        await self._client.aclose()
