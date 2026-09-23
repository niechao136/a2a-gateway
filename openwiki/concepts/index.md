# 文件

- [Agent 配置与绑定模型](agents-and-bindings.md) - 解释注册表与运行时 JSONB 快照的双层绑定、写时解析与刷新/解绑、技能绑定门禁、发布门控、保留 slug 规则及工厂缓存失效协同。
- [身份、会话与所有权](identity-and-conversations.md) - 解释单 httpOnly JWT 身份 cookie 的双身份设计、登录时会话幂等归并、目录与 checkpointer 分工、所有权校验与删除级联。
- [认证与安全面](security.md) - 梳理管理员 JWT、聊天身份 cookie、按 Agent API Key 三套凭据，出站认证方案、连接器验签、技能导入安全与沙箱隔离等信任边界。
- [技能（Skills）系统](skills.md) - 说明 SKILL.md 格式与限额常量、四种导入来源的安全校验、审核状态机、绑定门控、四层提示注入与沙箱脚本执行开关。
