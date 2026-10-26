# FastAPI 与 SSE 流式输出

## 为什么需要 SSE

大模型生成一段长文本可能需要数秒到数十秒。如果等完整结果再返回，用户会以为页面卡死。Server-Sent Events（SSE）让服务端可以持续向客户端推送文本增量，浏览器通过 `fetch` + `ReadableStream` 或 `EventSource` 消费。

SSE 的媒体类型是 `text/event-stream`，每条消息以 `data:` 开头，以两个换行结束：

```text
data: {"choices":[{"delta":{"content":"你"}}]}

data: [DONE]

```

## OpenAI 兼容的流式格式

OpenAI Chat Completions 的流式响应不是纯文本，而是 `chat.completion.chunk` 对象。客户端通常关心：

- `choices[0].delta.content`：本次新增的文本；
- `choices[0].finish_reason`：结束原因；
- 最后一条 `data: [DONE]`：流结束标记；
- `usage`：可选的 token 统计。

本项目在这个标准格式上增加了两个非标准字段：`x_phase` 表示 Agent 阶段，`x_agent` 表示报告元数据。标准客户端会忽略未知字段，自己的前端则可以利用它们做进度条和下载按钮。

## FastAPI 实现要点

1. 使用 `StreamingResponse`，不要用普通 `JSONResponse`。
2. 生成器必须是异步生成器；阻塞调用要放到 `asyncio.to_thread`。
3. 设置 `Cache-Control: no-cache` 与 `X-Accel-Buffering: no`，避免 Nginx 缓冲。
4. 客户端断开时会触发 `asyncio.CancelledError`，不要把它当成业务失败。
5. 流开始后 HTTP 状态码已经发送，后续错误只能通过 SSE 的 `event: error` 传递。

## 常见坑

- **代理缓冲**：Nginx 默认可能缓冲 SSE，需要关闭 `proxy_buffering`。
- **心跳缺失**：研究阶段可能几十秒没有 token，代理会超时；生产环境应定期发送注释行 `: ping`。
- **编码问题**：JSON 要使用 UTF-8，并设置 `ensure_ascii=False`，否则中文会变成转义序列。
- **背压**：客户端消费慢时，生成器会被阻塞；不要无限缓冲，必要时限制单次输出长度。
- **断连清理**：数据库写入、文件写入要在 `finally` 中处理，避免半成品。

## 非流式与流式的取舍

非流式实现简单，适合后台批处理；流式更适合聊天、报告生成等交互场景。两者应共享同一套业务服务，只是外层协议不同。ResearchCrew 的 `ChatService.stream()` 同时服务这两种模式。
