# 文件

- [配置体系与环境变量](configuration.md) - 说明 a2a-gateway 的 pydantic-settings 配置加载优先级、数据库连接串合成与迁移驱动改写，并按组件分类列出关键环境变量及默认值。
- [数据模型与迁移](data-model.md) - 说明 a2a-gateway 的 10 张应用表、快照与 id 列表并存的绑定存储、会话目录与 checkpointer 的分工，以及 Alembic 0001-0012 迁移与旧库接管机制。
- [部署拓扑与流量入口](deployment.md) - 说明 docker-compose 六服务拓扑、nginx 统一入口的路由/SSE/WS 规则、唯一对外端口策略、健康检查与后端 lifespan 启动序列。
- [系统架构总览](overview.md) - 解释 a2a-gateway 的三重角色、核心组件关系，以及一次请求穿过 nginx、FastAPI 路由、LangGraph 与持久层的控制/数据流。
