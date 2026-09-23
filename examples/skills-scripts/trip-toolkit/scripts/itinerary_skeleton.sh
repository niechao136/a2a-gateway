#!/usr/bin/env bash
# 行程骨架生成：stdin 每行一个活动 → Markdown 行程表，并追加 references/notes.md 的行前提醒。
# 沙箱内 cwd = 技能包根目录，故附件用相对路径读取。
#
# 用法：
#   printf '浅草寺\n明治神宫\n筑地市场\n' | bash scripts/itinerary_skeleton.sh 东京 2
# 参数：$1 目的地（缺省「未指定目的地」）、$2 每天活动数（缺省 3）
# 退出码：0 成功；2 stdin 无活动或参数非法。
set -euo pipefail

destination="${1:-未指定目的地}"
per_day="${2:-3}"
notes="references/notes.md"

if ! [[ "$per_day" =~ ^[0-9]+$ ]] || [ "$per_day" -lt 1 ]; then
  echo "每天活动数必须是正整数：$per_day" >&2
  exit 2
fi

activities="$(cat)"
if [ -z "${activities//[[:space:]]/}" ]; then
  echo "未从 stdin 读到活动；示例：printf '浅草寺\\n明治神宫\\n' | bash scripts/itinerary_skeleton.sh 东京 2" >&2
  exit 2
fi

echo "# ${destination} 行程骨架"
echo
echo "> 由 scripts/itinerary_skeleton.sh 生成：活动来自 stdin，提醒来自 ${notes}。"
echo

day=0
count=0
while IFS= read -r activity; do
  [ -n "$activity" ] || continue
  if [ "$count" -eq 0 ]; then
    day=$((day + 1))
    printf '## Day %s\n\n' "$day"
  fi
  printf -- '- [ ] %s\n' "$activity"
  count=$((count + 1))
  if [ "$count" -ge "$per_day" ]; then
    count=0
    echo
  fi
done <<< "$activities"

if [ -f "$notes" ]; then
  echo
  cat "$notes"
fi
