# 模型适配层设计

## 为什么要做适配层

不同模型服务的 API 细节不同：鉴权方式、base_url、模型命名、流式格式、错误码、token 统计都可能不一样。如果业务代码直接依赖某一家 SDK，切换模型时就要改很多地方。

适配层的目标不是“支持所有模型”，而是把差异收敛到少数几个扩展点：`base_url`、`api_key`、`model`、请求参数映射、响应解析。

## OpenAI 兼容协议的价值

OpenAI Chat Completions 已经成为事实标准。Ollama、vLLM、OneAPI、New API、LM Studio 等通常都提供兼容端点。只要遵守同一套请求/响应结构，前端、SDK、评测脚本都可以复用。

## 本项目的最小接口

```python
class BaseLLM:
    async def complete(messages, temperature, max_tokens) -> LLMResult: ...
    async def stream(messages, temperature, max_tokens) -> AsyncIterator[str]: ...
```

上层 Agent 只依赖这两个方法，不关心底层是 HTTP、SDK 还是本地推理。

## 错误处理

- 连接失败：返回 503，提示检查 Ollama / 代理是否启动；
- 上游 401：说明 Key 无效，不要重试；
- 429 / 5xx：有限次指数退避重试；
- 流式请求一旦开始输出，不再重试，否则会重复内容；
- 错误信息要包含 provider 和 URL，但绝不能打印 API Key。

## 生产化建议

1. 为每个 provider 增加熔断和超时；
2. 记录首 token 延迟、总延迟、token 用量；
3. 支持按租户 / 用户限流；
4. 敏感信息脱敏后再写日志；
5. 用配置中心管理模型路由，而不是改代码。
