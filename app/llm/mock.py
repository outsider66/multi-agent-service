"""离线 Mock 模型。

它不是为了“假装有智能”，而是为了：
1. 让项目在没有 API Key / 没有 Ollama 时也能完整跑通 API、SSE、报告、PDF；
2. 让自动化测试稳定、快速、无需网络；
3. 给真实模型输出提供一个结构参考（研究简报 / Markdown 报告应该长什么样）。
"""

from __future__ import annotations

import asyncio
import re
from typing import AsyncIterator, Dict, List, Optional

from app.llm.base import BaseLLM, LLMResult, estimate_messages_tokens, estimate_tokens


def _extract_topic(messages: List[Dict[str, str]]) -> str:
    text = "\n".join(item.get("content", "") for item in messages)
    for pattern in (
        r"用户任务[：:]\s*(.+)",
        r"【用户任务】\s*(.+)",
        r"研究主题[：:]\s*(.+)",
    ):
        match = re.search(pattern, text)
        if match:
            topic = match.group(1).strip().splitlines()[0].strip()
            return topic[:120] or "多智能体协作"
    for item in reversed(messages):
        if item.get("role") == "user" and item.get("content", "").strip():
            return item["content"].strip().splitlines()[0][:120]
    return "多智能体协作"


def _research_brief(topic: str) -> str:
    return f"""## 研究简报（离线 Mock）

**主题**：{topic}

**核心判断**：这是一个适合用“研究 → 写作”两阶段流水线处理的任务。先由研究 Agent 收敛事实与边界，再由写作 Agent 组织成可读报告，可以显著降低一次性生成长文时的跑题和结构混乱。

### 关键事实
1. OpenAI 兼容接口的核心是稳定的请求/响应结构，尤其是 `choices[].message.content` 与 SSE 增量格式。
2. 多智能体并不等于“更多模型”：每个 Agent 应有清晰职责、输入契约和可检查的中间产物。
3. 流式输出改善的是首字延迟和交互体验，不会自动降低总推理成本。
4. 报告类任务需要把“事实、推断、建议”分开，避免把不确定信息写成确定结论。

### 分析框架
- **问题定义**：明确读者、目标、范围和不做什么。
- **证据层**：区分本地资料、联网检索、模型常识三类来源。
- **写作层**：先结论后论据，再用表格和行动项降低阅读成本。
- **质量层**：检查引用、时效性、反例和风险边界。

### 风险与不确定性
- 离线 Mock 输出是固定模板，不能替代真实调研结论。
- 真实模型仍可能产生幻觉，需要引用校验与人工复核。
- 联网检索受 API 配额、网络和来源质量影响。

### 给写作 Agent 的建议
围绕“背景 → 关键发现 → 方案对比 → 风险 → 下一步行动”组织正文，并在开头给出一段 100 字以内的摘要。"""


def _report(topic: str) -> str:
    return f"""# {topic}：调研报告

> 生成模式：离线 Mock LLM。当前内容用于演示完整的服务链路，接入真实模型后会替换为实际调研结果。

## 一、摘要

围绕“{topic}”，本报告建议采用“研究 Agent + 报告撰写 Agent”的串行协作方式：研究 Agent 负责收敛事实、资料线索和分析框架，写作 Agent 负责把中间产物组织成面向读者的 Markdown 报告。该结构适合市场调研、技术选型、竞品分析和内部知识整理等场景。

## 二、背景与问题定义

在单模型一次性生成长报告时，常见问题包括：事实与观点混杂、结构不稳定、长文后半段质量下降、无法解释结论来自哪里。把任务拆成两个职责明确的阶段，可以让每一步都有可检查的中间结果。

本报告关注三个问题：

1. 如何用 OpenAI 兼容接口承接前端和业务系统？
2. 如何让多个 Agent 的协作过程可观察、可测试？
3. 如何把生成结果沉淀为可下载的报告资产？

## 三、关键发现

| 维度 | 观察 | 对实现的启示 |
| --- | --- | --- |
| 接口层 | OpenAI 协议已成为事实标准 | 保持 `/v1/chat/completions` 与 `/v1/models` 兼容 |
| 编排层 | 串行流水线最容易调试 | 先做 Researcher → Writer，再考虑并行/评审 |
| 流式层 | SSE 适合单向增量文本 | 用 `data:` 帧 + `[DONE]`，并处理断连 |
| 交付层 | 报告需要可追溯 | 保存 Markdown、元数据和 PDF，生成 report_id |

## 四、方案建议

### 4.1 接口与模型适配

将模型选择抽象为 `provider/model` 形式，例如 `ollama/qwen2.5:7b`、`openai/gpt-4o-mini`。业务层只依赖统一的 `complete` / `stream` 接口，不关心底层是官方 API、OneAPI 还是本地 Ollama。

### 4.2 多智能体编排

研究 Agent 的输出是“研究简报”，写作 Agent 的输入是“用户任务 + 研究简报 + 资料线索”。这种显式中间产物比让两个 Agent 自由聊天更可控，也更容易写测试。

### 4.3 流式交互

研究阶段通常不向用户逐字输出，只推送阶段状态；写作阶段再把 token 增量转发给客户端。这样既能让用户看到进度，又不会把中间过程混进最终报告。

### 4.4 报告交付

每次多智能体请求生成一个 `report_id`，同时保存 Markdown 和 JSON 元数据，并支持按需导出 PDF。PDF 负责“拿走”，Markdown 负责“继续编辑”，JSON 负责“追溯”。

## 五、风险与边界

- **事实风险**：模型可能编造数据、来源和时间；上线前应加入引用校验。
- **成本风险**：多 Agent 会增加调用次数，需要限制最大轮次、输入长度和并发。
- **安全风险**：检索到的网页可能包含提示注入，必须把资料当数据而不是指令。
- **体验风险**：研究阶段耗时较长，SSE 需要心跳或阶段提示，避免连接被代理断开。

## 六、下一步行动

1. 用真实 Ollama 模型跑同一组问题，对比 Mock 输出与真实输出的结构差异。
2. 给研究简报增加“来源列表”，并在正文中使用 `[1]` 形式引用。
3. 增加 Reviewer Agent，在写作完成后检查事实、结构和引用。
4. 为 `/v1/chat/completions` 增加限流、幂等键和可观测性指标。

## 附：说明

本报告由 ResearchCrew 离线演示模式生成。它展示的是工程链路和报告结构，不构成事实性研究结论。"""


class MockLLM(BaseLLM):
    provider = "mock"
    upstream_model = "demo"

    async def complete(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: Optional[int] = None,
    ) -> LLMResult:
        system = "\n".join(
            item.get("content", "") for item in messages if item.get("role") == "system"
        )
        topic = _extract_topic(messages)
        # 用“角色声明”而不是普通关键词判断，避免研究提示词里出现“报告撰写”
        # 或写作提示词里出现“研究简报”时串角色。
        if "「报告撰写 Agent」" in system:
            text = _report(topic)
        elif "「研究员 Agent」" in system:
            text = _research_brief(topic)
        elif "「报告审核 Agent」" in system:
            text = "审核结果：通过\n\n原因：离线 Mock 模式下没有可核查的事实内容，按通过处理。"
        else:
            text = (
                f"这是 ResearchCrew 的离线 Mock 回复。你问的是：{topic}\n\n"
                "当前没有配置真实模型，因此我返回固定示例。把 DEFAULT_MODEL 改成 "
                "`ollama/qwen2:latest` 或 `openai/gpt-4o-mini`，并配置对应服务后即可获得真实回答。"
            )
        prompt_tokens = estimate_messages_tokens(messages)
        completion_tokens = estimate_tokens(text)
        return LLMResult(
            content=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            upstream_model=self.upstream_model,
        )

    async def stream(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        result = await self.complete(messages, temperature=temperature, max_tokens=max_tokens)
        # 按固定长度切片，模拟真实模型的 token 增量；await 0 让出事件循环。
        step = 22
        for index in range(0, len(result.content), step):
            yield result.content[index : index + step]
            await asyncio.sleep(0)
