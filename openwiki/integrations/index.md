# 文件

- [A2A 上游客户端](a2a-client.md) - 说明 A2AClientWrapper 的 Agent Card 发现、接口 URL 重写、流式事件抽取、三类错误分类与瞬态重试策略（有输出后不再重试）及连通性测试。
- [对外 A2A 服务端](a2a-server.md) - 说明 Agent Card 发布与 X-Forwarded 派生公网 URL、JSON-RPC 方法别名分发、流式任务帧协议（WORKING→COMPLETED/INPUT_REQUIRED）、API Key 鉴权与无状态任务语义。
- [聊天平台连接器](connectors.md) - 说明 Feishu/Telegram/Slack 入站连接器：适配器接口（验签/归一化/发送）、去重与每会话串行队列管线、线程映射、凭据掩码与 webhook URL 派生。
- [MCP 集成](mcp.md) - 说明 MCP 服务端注册表到工具的映射：三种传输（stdio/sse/streamable_http）会话打开、工具探测与调用、绑定工具与回退 mcp_call、提示格式化与超时。
- [语音代理（ASR/TTS）](speech.md) - 说明对 onnx-hub 的 TTS HTTP 流式代理与 ASR WebSocket 双向泵转发、API Key 注入与对前端隐藏、错误映射（502/503/4502/4503）。
