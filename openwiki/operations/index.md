# 文件

- [故障处理与可观测性](failure-and-observability.md) - 汇总失败语义：全局 500 兜底、A2A 错误分类、SSE 错误不泄漏策略、告警 webhook 的降级、关键超时清单与故障弱化（fail-soft）模式。
- [沙箱执行（gate/runner 隔离）](sandbox.md) - 说明技能脚本执行的双容器隔离：gate 前门（Bearer、限流、8MB 上限）经 unix socket 转发到无网络 runner（解释器白名单、路径校验、rlimits、环境白名单），及客户端三类错误。
- [测试策略与验证](testing.md) - 说明零外部依赖的测试哲学（dependency_overrides + ASGITransport + monkeypatch）、各测试簇守护的行为、e2e smoke 与沙箱独立测试套件、前端 vitest 覆盖。
