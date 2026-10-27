"""Pydantic 请求 / 响应模型。

这里刻意保留了 OpenAI Chat Completions 的核心字段，同时增加四个教学用的扩展字段：
- mode: single / multi_agent
- export_pdf: 是否在多智能体报告生成后自动导出 PDF
- metadata: 调用方自定义的追踪信息（原样回传）
- report_versions: 审核未通过时保存草稿 + 最终版（both），还是只保存最终版（final）
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: Union[str, List[Dict[str, Any]], None] = None
    name: Optional[str] = None

    def text(self) -> str:
        """把 OpenAI 的字符串 / 多段 content 统一成纯文本。"""
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            parts: List[str] = []
            for part in self.content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
            return "".join(parts)
        return ""


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: Optional[str] = None
    messages: List[ChatMessage] = Field(..., min_length=1)
    stream: bool = False
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    top_p: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=32768)
    max_completion_tokens: Optional[int] = Field(default=None, ge=1, le=32768)

    # 本项目扩展字段
    mode: Literal["single", "multi_agent"] = "single"
    export_pdf: bool = False
    metadata: Optional[Dict[str, Any]] = None
    stream_options: Optional[Dict[str, Any]] = None
    # 审核未通过时是否保留草稿版：both = 草稿 + 最终版都存，final = 只存最终版
    report_versions: Literal["final", "both"] = "both"

    @model_validator(mode="after")
    def _validate_messages(self) -> "ChatCompletionRequest":
        if not self.last_user_message():
            raise ValueError("messages 中必须包含一条非空的 user 消息")
        return self

    def last_user_index(self) -> int:
        for index in range(len(self.messages) - 1, -1, -1):
            if self.messages[index].role == "user" and self.messages[index].text().strip():
                return index
        return -1

    def last_user_message(self) -> str:
        index = self.last_user_index()
        return self.messages[index].text().strip() if index >= 0 else ""

    def history(self) -> List[ChatMessage]:
        index = self.last_user_index()
        return self.messages[:index] if index >= 0 else []

    @property
    def effective_max_tokens(self) -> Optional[int]:
        return self.max_completion_tokens or self.max_tokens


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class PDFToolRequest(BaseModel):
    title: Optional[str] = None
    markdown: str = Field(..., min_length=1)
    filename: Optional[str] = None


class ModelCard(BaseModel):
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "researchcrew"


def new_completion_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex[:24]
