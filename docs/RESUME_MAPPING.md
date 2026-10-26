# 简历映射与面试讲法

这份文档帮你把代码和简历上的项目描述对应起来，同时避免“写了但讲不出来”或“把教学项目说成生产系统”。

---

## 1. 简历要点 → 代码证据

| 简历描述 | 代码位置 | 面试时能讲什么 |
| --- | --- | --- |
| 基于 FastAPI 实现 `/v1/chat/completions` 通用接口 | `app/api/routes_chat.py`、`app/schemas.py` | 请求校验、OpenAI 字段兼容、非流式/流式同一业务服务 |
| 兼容 OpenAI 调用格式 | `app/api/routes_chat.py` | `chat.completion` 与 `chat.completion.chunk` 的结构、`[DONE]`、错误对象 |
| 多模型适配层 | `app/llm/` | `provider/model` 路由、base_url/Key 差异收敛、客户端缓存 |
| 支持国内外大模型、Ollama、OneAPI | `app/llm/registry.py`、`openai_compat.py` | 同一 HTTP 协议适配多个端点；未配置 Key 的明确报错 |
| CrewAI 拆分研究 Agent / 报告撰写 Agent | `app/engine/multi_agent.py`、`crewai_engine.py` | 串行协作、显式中间产物、为什么默认内置编排 |
| 普通返回 + SSE 流式 | `app/service.py`、`routes_chat.py` | 阶段事件、token 增量、断连、流式错误不能再改状态码 |
| 集成文本转 PDF | `app/tools/pdf.py` | 中文排版、字体子集化、分页、表格、页码 |
| 文档导出 | `app/reports/store.py` | report_id、Markdown/JSON/PDF 三种资产 |
| 自动化测试 | `tests/` | 13 项离线测试，覆盖协议、SSE、鉴权、PDF、检索 |
| 可对接前端/业务系统 | `app/static/index.html`、README API 示例 | OpenAI SDK / curl / 自研前端都能接 |

---

## 2. 简历 bullet 的可替换写法

不要直接复制成“我上线了一个系统”。下面这些写法都以本项目真实代码为依据，按你的实际投入选择。

### 写法 A：偏后端 / 接口

> 基于 FastAPI 设计并实现 OpenAI 兼容的 Chat Completions 服务，支持 `/v1/chat/completions`、`/v1/models`、普通响应与 SSE 流式响应；通过 provider/model 路由统一接入 OpenAI、OneAPI、Ollama 等兼容端点，并使用 Mock Provider 完成无外部依赖的自动化测试。

### 写法 B：偏多智能体

> 设计“研究员 Agent → 报告撰写 Agent”的串行协作流程：研究员负责检索资料、输出结构化研究简报，写作 Agent 基于简报生成 Markdown 报告；通过显式中间产物、阶段事件和 report_id 提升可观测性与可测试性。

### 写法 C：偏工程化

> 为多智能体报告服务补齐报告资产链路：每次请求持久化 Markdown 与 JSON 元数据，支持按需导出中文 PDF；PDF 模块处理字体发现、字体子集化、分页、表格与页码，并通过 13 项离线测试覆盖接口、SSE、鉴权、Provider 路由和导出。

### 写法 D：如果确实使用了 CrewAI

> 使用 CrewAI 的 Agent / Task / Crew / Process.sequential 编排研究与写作角色，并通过自定义 LLM 配置接入 Ollama / OpenAI 兼容端点；同时保留自研编排器作为轻量降级路径。

> 注意：只有你实际安装并跑通 CrewAI 版本后，才使用写法 D。默认项目跑的是内置编排器。

---

## 3. 三分钟演示脚本

### 0:00–0:30 问题

“很多团队接大模型时，前端要对接多家模型，长报告又容易结构乱、等得久，生成完还留不下来。我做了一个教学版服务，把这几件事串起来。”

### 0:30–1:10 架构

“最外层是 OpenAI 兼容接口，业务层不直接依赖某家 SDK，而是通过 LLM Registry 路由到 Mock、Ollama、OpenAI 或 OneAPI。多 Agent 模式是研究员先检索资料、生成研究简报，写作 Agent 再基于简报流式写报告。”

### 1:10–2:20 演示

- 打开 UI，发一个多 Agent 问题；
- 指出阶段时间线：检索 → 研究 → 写作；
- 指出正文是流式出现的；
- 展示 `report_id`、来源列表、PDF 下载；
- 打开 `data/reports/<id>/report.md` 和 `report.json`。

### 2:20–3:00 取舍与边界

“我默认用手写编排而不是 CrewAI，是为了让数据流和中间产物可检查；CrewAI 作为可选适配器。PDF 用 PyMuPDF 是为了不依赖 Office。生产还缺限流、队列、多租户和评测，我在文档里列了扩展路线。”

---

## 4. 高频追问与回答

### Q：为什么多 Agent 能提升质量？

A：不是“Agent 数量”本身，而是任务分解后每一步的约束更强。研究员只负责收敛事实和框架，写作只负责组织和表达，减少了单次长生成中的角色冲突。代价是调用次数、延迟和成本增加，所以简单问答不应该默认走多 Agent。

### Q：怎么保证两个 Agent 不互相“踢皮球”？

A：本项目是单向串行，不让 Agent 自由对话；研究简报是显式输入，写作任务有明确边界。没有采用“无限委派”的层级模式，是为了可控和可测试。

### Q：SSE 和 WebSocket 怎么选？

A：报告生成是服务端到客户端的单向增量，SSE 足够，协议简单、兼容 HTTP、断线重连成本低；需要双向实时协作时再考虑 WebSocket。

### Q：流式输出中途模型挂了怎么办？

A：HTTP 状态码已经发出，不能改成 500；服务端发送 `event: error`，再发 `[DONE]` 结束。前端保留已生成内容并提示重试。生产还应加幂等键，避免重试导致重复计费。

### Q：怎么测试多模型适配？

A：Provider 层用 `httpx.MockTransport` 模拟上游响应，验证请求体、鉴权头、SSE 解析和错误映射；不需要真实 Key。业务层用 MockLLM 验证编排和报告链路。真实模型再做少量 smoke test。

### Q：为什么报告存文件而不是数据库？

A：教学项目追求零外部依赖，Markdown/PDF 天然是文件；`ReportStore` 接口固定，生产可以替换成 PostgreSQL + 对象存储。面试时主动说这是取舍，不是“不会数据库”。

### Q：PDF 中文乱码怎么处理？

A：按平台寻找中文字体（Windows 微软雅黑/黑体、macOS PingFang、Linux Noto CJK），找不到时用 PyMuPDF 内置 CJK 字体；注册字体后用字体对象测量宽度做换行；保存前做字体子集化，避免 PDF 体积过大。

### Q：这个项目有什么不足？

A：没有用户体系、限流、任务队列、多租户隔离、完整评测；检索是轻量 bigram + IDF，不是向量检索；CrewAI 适配器是阶段级流式，不是 token 级；PDF 表格只支持简单结构。把这些说清楚，比强行包装更加分。

---

## 5. 不要这样写 / 说

- ❌ “支撑百万级用户” —— 项目没有压测和用户体系；
- ❌ “生产级高可用” —— 没有队列、限流、多副本、故障演练；
- ❌ “CrewAI 深度定制 + token 级流式” —— 默认路径是内置编排，CrewAI 适配器也未做 token 级转发；
- ❌ “自研 PDF 引擎” —— 是基于 PyMuPDF 的排版层，不要夸大；
- ❌ “完全消除幻觉” —— 只能降低，需要引用校验和人工复核；
- ❌ 把 Mock 输出当成真实调研结论 —— 它只是链路演示。

---

## 6. 如果只有 30 分钟准备面试

按这个顺序：

1. 跑一次 `python scripts/demo.py ... --mode multi_agent --pdf`；
2. 打开 `app/service.py`，记住 `prepare → stream → _persist_report`；
3. 打开 `app/api/routes_chat.py`，记住 SSE chunk 的三个关键字段；
4. 打开 `app/llm/registry.py`，记住 `provider/model` 解析和缺失 Key 报错；
5. 打开 `data/reports/<id>/`，确认三种产物；
6. 背下“三个取舍”：默认内置编排、文件存储、Mock 可测试；
7. 背下“三个不足”：限流/队列/多租户，检索不是向量，CrewAI 非 token 级流式。

这样即使被追问细节，也能落到真实代码，而不是背概念。
