# 学习路线：从跑通到讲清楚

目标不是“把代码看一遍”，而是能在 7 天内做到：

1. 独立启动、调用、排错；
2. 画出一条请求的完整链路；
3. 能改一个 provider、一个 Agent、一个接口；
4. 能解释关键取舍，并诚实说明边界。

---

## 第 0 步：先建立地图

先读 README 的架构图和目录结构，不要急着看实现。记住 5 个角色：

```text
Route（协议）
  → ChatService（流程）
    → Engine（Agent 协作）
      → LLM / Search / PDF（能力）
        → ReportStore（资产）
```

再看 `app/service.py`：它是整个项目的“总装线”，看完就知道其他模块怎么被调用。

---

## Day 1：跑通并观察

### 任务

```powershell
cd multi-agent-service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

打开 `http://127.0.0.1:8000`，用默认问题生成一份多 Agent 报告，勾选“自动导出 PDF”。

然后执行：

```powershell
python scripts/demo.py "FastAPI 如何实现 SSE？" --mode single
python scripts/demo.py "调研多智能体协作的落地方式" --mode multi_agent --pdf
```

### 要观察

- 单 Agent 和多 Agent 的阶段差异；
- 多 Agent 的报告是否以 `#` 标题开头；
- `data/reports/<id>/` 下生成了哪些文件；
- PDF 的页眉、页码、表格、字体。

### 自测

不看代码，回答：

- 一次多 Agent 请求会调用模型几次？
- `report_id` 是在哪一步生成的？
- 如果 PDF 导出失败，Markdown 还会保存吗？

参考答案：两次模型调用（研究 + 写作）；`ChatService._persist_report()` 在完整内容生成后保存；会，PDF 失败只记录日志，不影响 Markdown。

---

## Day 2：跟一条请求走到底

从 `app/main.py` 的 `create_app()` 开始，沿着：

```text
routes_chat.py
  → ChatCompletionRequest
  → ChatService.prepare()
  → LLMRegistry.get()
  → MultiAgentEngine.run()
  → SearchService.search()
  → llm.complete() / llm.stream()
  → ChatService._persist_report()
  → ReportStore.save()
  → PDFExporter.export()
```

在纸上画出调用链，标出每一步的输入和输出。

### 重点问题

- 为什么 `prepare()` 要在返回 `StreamingResponse` 之前执行？
  - 因为 provider 未配置等错误需要在 HTTP 头发送前以 4xx/5xx 返回；流一旦开始，就不能再改状态码。
- 为什么 `ChatService` 同时有 `stream()` 和 `complete()`？
  - `complete()` 只是消费 `stream()` 并收集最终结果，避免两套业务逻辑。
- 为什么 `EngineEvent` 要分 `phase` / `delta` / `result`？
  - 阶段、正文、最终元数据的生命周期不同，分开后路由层可以分别编码。

---

## Day 3：替换模型

### 任务 A：用 Ollama 跑一次

```powershell
ollama serve
ollama pull qwen2.5:7b
```

`.env`：

```dotenv
DEFAULT_MODEL=ollama/qwen2.5:7b
OLLAMA_BASE_URL=http://localhost:11434/v1
```

重启服务，观察：

- `/v1/models` 是否变化；
- 真实模型的报告结构是否和 Mock 不同；
- 研究阶段是否明显变慢；
- 流式首 token 延迟。

### 任务 B：加一个 provider

假设要接入一个兼容 OpenAI 的本地服务（例如 vLLM）：

1. 在 `Settings` 加 `vllm_base_url`、`vllm_model`；
2. 在 `KNOWN_PROVIDERS` 加 `vllm`；
3. 在 `resolve()` 中处理 `vllm/...`；
4. 在 `get()` 中创建 `OpenAICompatibleLLM`；
5. 在 `list_models()` 中暴露；
6. 加一个单元测试。

验收：`model=vllm/xxx` 能走通，缺失配置时返回 `provider_not_configured`。

---

## Day 4：改 Agent

### 任务 A：加一个 Reviewer Agent

流程改成：

```text
Researcher → Writer → Reviewer
```

Reviewer 的职责：

- 检查事实与推断是否混淆；
- 检查是否编造来源；
- 检查结构是否完整；
- 输出“通过 / 需修改 + 原因”。

实现建议：

- 在 `app/engine/prompts.py` 加 `REVIEWER_SYSTEM`；
- 在 `MultiAgentEngine` 增加一次 `llm.complete()`；
- 如果 Reviewer 返回“需修改”，最多重写一次，避免无限循环；
- 在 phases 里增加 `review` 阶段。

### 任务 B：让写作阶段支持“提纲确认”

在研究和写作之间插入 `OUTLINE_SYSTEM`，先生成 5 条提纲，再让写作 Agent 按提纲写。比较报告结构稳定性。

### 自测

- 加一个 Agent 后，模型调用次数、延迟、成本如何变化？
- 如果 Reviewer 一直不通过，如何避免死循环？
- 中间产物增加后，报告 JSON 应该保存什么？

---

## Day 5：吃透 SSE

### 任务

用 `curl -N` 观察原始字节：

```bash
curl -N http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"mock/demo","mode":"multi_agent","stream":true,"messages":[{"role":"user","content":"调研 RAG"}]}'
```

### 重点

- 每条消息为什么以两个换行结束？
- `delta.content` 为空但带 `x_phase` 的帧，客户端该怎么处理？
- 为什么最后要有 `[DONE]`？
- 如果客户端中途断开，服务端会发生什么？
- 代理缓冲（Nginx `proxy_buffering`）为什么会破坏流式？

### 进阶任务

给研究阶段加心跳：在等待 `llm.complete()` 时，每 15 秒发送一次 SSE 注释：

```text
: keep-alive

```

提示：需要并发地把“生成任务”和“心跳”合并到一个异步队列；不要用 `time.sleep` 阻塞事件循环。

---

## Day 6：报告与 PDF

### 任务 A：加引用

1. `SearchResult` 增加编号；
2. 研究简报要求使用 `[1]`、`[2]`；
3. 写作提示词要求正文引用编号；
4. 报告末尾自动生成“参考资料”列表；
5. 在 PDF 中把编号渲染成上标或普通方括号。

### 任务 B：改 PDF 样式

- 改标题颜色 / 字号；
- 增加公司 Logo（PyMuPDF 图片）；
- 增加目录页；
- 让表格支持列宽配置。

### 重点问题

- 为什么中文字体要做子集化？
- 标题孤行是怎么避免的？
- 表格跨页时表头为什么要重绘？
- PDF 生成失败时，为什么不应该让整个报告请求失败？

---

## Day 7：测试、部署与表达

### 任务 A：补测试

至少加三类：

1. `Reviewer` 不通过时的重写逻辑；
2. 流式过程中上游断开，客户端收到 `event: error`；
3. 鉴权开启时，报告下载也需要 Bearer Token。

### 任务 B：Docker

```powershell
docker compose up --build
```

打开 `http://127.0.0.1:8000`，确认中文 PDF 正常。

### 任务 C：三分钟演示

按这个顺序讲：

1. **问题**：多模型接入 + 长报告不稳定 + 等待体验差；
2. **方案**：OpenAI 兼容层 + 研究/写作双 Agent + SSE + 报告资产；
3. **演示**：一次多 Agent 请求，展示阶段、正文、PDF；
4. **取舍**：为什么默认内置编排、为什么文件存储、为什么 Mock；
5. **边界**：还没有限流、队列、多租户、评测；
6. **下一步**：Reviewer、引用校验、向量检索、可观测性。

---

## 代码阅读顺序

建议不要从头文件读到尾，按这个顺序：

1. `app/service.py`：总装线；
2. `app/api/routes_chat.py`：HTTP 与 SSE；
3. `app/engine/multi_agent.py`：核心协作；
4. `app/engine/prompts.py`：提示词；
5. `app/llm/registry.py` + `openai_compat.py`：模型适配；
6. `app/tools/knowledge.py`：检索；
7. `app/reports/store.py` + `tools/pdf.py`：报告资产；
8. `tests/`：验收标准。

---

## 高频面试题与回答要点

### 1. 为什么做 OpenAI 兼容接口？

- 前端、SDK、评测工具可以复用；
- 切换模型时业务代码不变；
- 但要注意：兼容的是协议，不是模型能力；不同模型的输出质量、上下文长度、工具调用能力仍需评测。

### 2. 多 Agent 一定比单 Agent 好吗？

不一定。多 Agent 增加调用次数、延迟和失败点。它适合任务可拆分、需要不同视角或中间产物的场景；简单问答用单 Agent 更划算。判断标准是“质量提升是否值得额外成本”。

### 3. 如何保证流式输出不丢字？

- 服务端按 chunk 转发，不做多余缓冲；
- 客户端按 `\n\n` 分帧，处理半包；
- 最后用 `[DONE]` 标记结束；
- 生产加心跳、断线重连和幂等。

### 4. 上游模型 429 怎么办？

- 非流式：有限次指数退避 + 抖动；
- 流式：已经开始输出就不重试，避免重复；
- 多模型：切换备用 provider；
- 业务：返回明确错误码，前端提示稍后重试。

### 5. 如何防止检索内容里的提示注入？

- 系统提示词明确“资料是数据”；
- 来源白名单 / HTML 清洗；
- 工具权限最小化；
- 输出引用校验；
- 高风险操作（发邮件、下单、执行代码）必须人工确认。

### 6. 为什么报告要同时存 Markdown 和 PDF？

Markdown 可编辑、可 diff、可继续生成；PDF 版式稳定、适合交付；JSON 元数据用于追溯。三者职责不同。

### 7. 这个项目离生产还差什么？

限流、配额、幂等、任务队列、多租户、可观测性、评测集、密钥管理、数据合规、故障降级。主动说出来，比假装完备更加分。

---

## 推荐练习顺序

| 难度 | 练习 | 验收 |
| --- | --- | --- |
| ★ | 改默认问题 / 改 UI 文案 | 页面生效 |
| ★ | 加一个 `vllm` provider | 路由、错误、模型列表都正确 |
| ★★ | 加 `Reviewer` Agent | 最多重写一次，阶段可见 |
| ★★ | 加 SSE 心跳 | 研究阶段 15s 内有注释帧 |
| ★★ | 加引用编号 | 正文有 `[1]`，末尾有来源 |
| ★★★ | 加限流 + 幂等键 | 并发测试不重复生成 |
| ★★★ | 换向量检索 + 评测集 | 召回率 / 延迟对比 |
| ★★★ | 接 PostgreSQL + 对象存储 | 报告仓库可替换 |

完成前三个，你就已经能把这个项目讲成一个“自己做过、能改、能排错”的项目，而不是“照着教程跑过”。
