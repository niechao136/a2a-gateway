---
name: sandbox-probe
description: 沙箱行为探针（测试夹具）：退出码与 stderr、超 32KB 输出截断、超时 kill 进程组，用于验证 Skill 脚本执行链路与试跑面板
---

# 沙箱行为探针（测试夹具）

本技能不承载业务能力，专门用于验证脚本执行链路的边界行为。
管理端「详情 → 试跑」或 `scripts/build_skill_test_packages.py --execute`
都应命中下表结果；**不要**把它绑定到面向用户的 Agent。

| 脚本 | 预期结果 |
| --- | --- |
| `scripts/exit_nonzero.py` | `exit_code=3`；stdout、stderr 各一行 |
| `scripts/noisy.py` | `exit_code=0`；stdout 40000 字节 → `truncated=true`（截断到 32768） |
| `scripts/slow.py` | 默认 30s 超时内不结束 → `timeout=true`；试跑或真跑请把超时设为 3~5 秒 |

## 说明

- `slow.py` 设计为睡 600 秒：超时后 runner 会 kill 整个进程组，且**不回传半截输出**
  （规格 §9 约定，stdout 为空属预期，不要当成 bug）。
- `noisy.py` 每行 80 字节 × 500 行 = 40000 字节，刚好越过 32768 字节截断阈值，
  用于确认「输出已截断」提示与 `truncated` 标记同时生效。
- `exit_nonzero.py` 验证非零退出码不会中断对话：Agent 侧表现为工具文本输出，
  试跑面板表现为红色 exit_code，仍返回 stdout / stderr。
