"""行程预算估算：读 references/price-ranges.csv，按命令行参数输出分项与合计。

沙箱内 cwd = 技能包根目录，故附件用相对路径打开；只用标准库。

用法：
    python3 scripts/budget_estimate.py --city 东京 --nights 5 --travelers 2 --level mid

退出码：0 成功；2 城市/档位不在价目表（stdout 会列出可选值）。
"""

import argparse
import csv
import pathlib
import sys

PRICE_CSV = pathlib.Path("references/price-ranges.csv")
LEVELS = ("low", "mid", "high")
FLEX_PERCENT = 20  # 机动预算比例（%）


def load_prices() -> dict[str, dict[str, dict[str, int]]]:
    """价目表 → {城市: {档位: {hotel/food/transport 单价}}}。"""
    with PRICE_CSV.open(encoding="utf-8", newline="") as fp:
        rows = list(csv.DictReader(fp))
    prices: dict[str, dict[str, dict[str, int]]] = {}
    for row in rows:
        city = (row.get("city") or "").strip()
        level = (row.get("level") or "").strip()
        if not city or level not in LEVELS:
            continue
        prices.setdefault(city, {})[level] = {
            "hotel": int(row["hotel_per_night"]),
            "food": int(row["food_per_day"]),
            "transport": int(row["transport_per_day"]),
        }
    return prices


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="行程预算估算（读 references/price-ranges.csv）")
    parser.add_argument("--city", default="东京", help="目的地城市")
    parser.add_argument("--nights", type=int, default=3, help="住宿晚数")
    parser.add_argument("--travelers", type=int, default=1, help="出行人数")
    parser.add_argument("--level", choices=LEVELS, default="mid", help="消费档位")
    args = parser.parse_args(argv)

    prices = load_prices()
    if args.city not in prices:
        print(f"价目表未收录城市：{args.city}")
        print("可选城市：" + "、".join(sorted(prices)))
        return 2
    rate = prices[args.city].get(args.level)
    if rate is None:
        print(f"{args.city} 缺少 {args.level} 档价目")
        print("可选档位：" + "、".join(sorted(prices[args.city])))
        return 2

    nights = max(1, args.nights)
    travelers = max(1, args.travelers)
    lines = [
        f"目的地：{args.city}（{args.level} 档，单价来自 references/price-ranges.csv）",
        f"行程：{nights} 晚 / {travelers} 人",
    ]
    subtotal = 0
    for name, unit, count, unit_label in (
        ("住宿", rate["hotel"], nights, "晚"),
        ("餐饮", rate["food"], nights, "天"),
        ("市内交通", rate["transport"], nights, "天"),
    ):
        amount = unit * count * travelers
        subtotal += amount
        lines.append(f"{name}：{unit} 元 × {count} {unit_label} × {travelers} 人 = {amount} 元")
    flexible = round(subtotal * FLEX_PERCENT / 100)
    lines.append(
        f"小计：{subtotal} 元；含 {FLEX_PERCENT}% 机动后建议预算：{subtotal + flexible} 元"
    )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
