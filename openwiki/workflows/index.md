# 文件

- [Agent 图构建与编排](agent-graph.md) - 解释共享 ReAct 图的构建：pre_model_hook 的历史压缩、pending 提示与技能再注入，工具组装（A2A/MCP/技能），以及 LLM 适配层的 thought_signature 兼容处理。
- [聊天请求生命周期（SSE）](chat-lifecycle.md) - 端到端追踪一次聊天回合：slug 解析、身份 cookie、会话 upsert、工厂取图、graph 事件到 SSE 事件契约、回合末 pending 检查、时间旅行重试与错误/告警路径。
- [input-required 与恢复流程](input-required-resume.md) - 解释下游 InputRequired 如何写入 pending 存储、SSE interrupt 事件、下一回合提示注入，以及聊天链（thread_id）与 A2A 链（external task_id）两条恢复路径与 TTL 过期语义。
