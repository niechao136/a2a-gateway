---
name: trip-toolkit
description: 行程工具包（脚本执行示例）：预算估算 / 行程骨架 / 打包清单三个脚本，覆盖 argv 传参、stdin 输入与读取技能包内附件
---

# 行程工具包（脚本执行示例）

本技能演示「技能捆绑脚本」的三种典型用法。沙箱内工作目录 = 技能包根目录，
脚本只能用镜像预置运行时（python3 / bash / node）与标准库，且无网络。
脚本输出是草稿，交付用户前必须人工复核金额与日期。

## scripts/budget_estimate.py

读 `references/price-ranges.csv`（单价为「每人每晚 / 每人每天」的人民币参考价）估算预算：

```
python3 scripts/budget_estimate.py --city 东京 --nights 5 --travelers 2 --level mid
```

- 档位 `--level` 取 `low` / `mid` / `high`，缺省 `mid`
- 城市不在价目表时 **退出码 2**，并在 stdout 列出可选城市（用于验证失败路径）

## scripts/itinerary_skeleton.sh

从 **stdin** 读活动清单（每行一个），按每天 N 项生成 Markdown 行程骨架，
再把 `references/notes.md` 的行前提醒追加到文末：

```
printf '浅草寺\n明治神宫\n筑地市场\n' | bash scripts/itinerary_skeleton.sh 东京 2
```

- 参数：`$1` 目的地（缺省「未指定目的地」）、`$2` 每天活动数（缺省 3）
- stdin 为空时 **退出码 2**，提示调用方式

## scripts/packing_list.js

读 `references/packing-rules.json`，按城市 / 季节 / 天数生成勾选式打包清单：

```
node scripts/packing_list.js --city 东京 --season winter --days 5
```

- 天数达到 `long_trip.threshold_days` 时追加长途项
- 未收录的城市或季节只在 stderr 提示，仍输出通用清单（退出码 0）
