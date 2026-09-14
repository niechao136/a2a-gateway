"""一次性回填：把 Checkpointer 里的历史 thread 登记为某个账号的会话。

背景
----
会话目录（conversations 表）是后加的。此前产生的 thread 只存在于 LangGraph
Checkpointer 的 checkpoints 表里、没有任何归属记录，因此在对话页侧边栏里
**看不到也找不到**（本次排查出的「周末换机器看不到对话」就是这个问题）。

本脚本把这些「孤儿会话」补登记到指定账号名下。消息本体按 thread_id 存储、
与归属无关，因此回填后历史消息原地可续，不需要任何数据搬迁。

用法
----
容器内执行（账号默认取环境变量 ADMIN_USERNAME）：:

    docker cp scripts/backfill_conversations.py a2a-gateway-backend:/tmp/
    docker compose exec -T backend python /tmp/backfill_conversations.py

可选参数：
    --username  指定归属账号（默认 ADMIN_USERNAME）
    --dry-run   只打印统计，不写库
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from a2a_gateway.config import get_settings
from a2a_gateway.database import AsyncSessionLocal
from a2a_gateway.identity import IDENTITY_KIND_USER
from a2a_gateway.models import Conversation
from a2a_gateway.repository import normalize_slug

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill")

TITLE_MAX = 24


def _parse_ts(value) -> datetime | None:
    """Checkpointer 的 ts 是 ISO 字符串（可能带 Z）；解析失败返回 None。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _first_human_text(messages) -> str:
    """取第一条人类消息作为标题（与前端「首条消息定标题」保持一致）。"""
    for msg in messages or []:
        if getattr(msg, "type", "") == "human":
            content = getattr(msg, "content", "")
            if isinstance(content, str):
                return content[:TITLE_MAX]
            return str(content)[:TITLE_MAX]
    return ""


async def backfill(username: str, dry_run: bool = False) -> int:
    settings = get_settings()
    saver_cm = AsyncPostgresSaver.from_conn_string(settings.sync_db_url)
    saver = await saver_cm.__aenter__()
    try:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(text("select distinct thread_id from checkpoints"))
            ).scalars().all()
            known = set(
                (await session.execute(select(Conversation.thread_id))).scalars().all()
            )
            pending = [t for t in rows if t and t not in known]

            logger.info(
                "Checkpointer 中共 %s 个 thread，已登记 %s 个，待回填 %s 个",
                len(rows),
                len(known),
                len(pending),
            )
            if not pending:
                return 0

            values = []
            for thread_id in pending:
                title = ""
                stamp = None  # 检查点时间：回填后用作会话创建 / 更新时间
                try:
                    snapshot = await saver.aget_tuple(
                        {"configurable": {"thread_id": thread_id}}
                    )
                    if snapshot is not None:
                        messages = snapshot.checkpoint.get("channel_values", {}).get(
                            "messages", []
                        )
                        title = _first_human_text(messages)
                        stamp = _parse_ts(snapshot.checkpoint.get("ts"))
                except Exception:
                    logger.warning("读取 thread %s 失败，标题留空", thread_id, exc_info=True)
                row = {
                    "thread_id": str(thread_id)[:64],
                    "agent_slug": normalize_slug("/"),
                    "title": title,
                    "owner_kind": IDENTITY_KIND_USER,
                    "owner_id": username,
                }
                # 保留真实时间，避免所有回填会话都显示成「今天」
                if stamp is not None:
                    row["created_at"] = stamp
                    row["updated_at"] = stamp
                values.append(row)

            if dry_run:
                for item in values:
                    logger.info("[dry-run] %s → %s", item["thread_id"], item["title"] or "(无标题)")
                return len(values)

            await session.execute(
                pg_insert(Conversation)
                .values(values)
                .on_conflict_do_nothing(index_elements=["thread_id"])
            )
            await session.commit()
            logger.info("已回填 %s 条会话到账号 %s", len(values), username)
            return len(values)
    finally:
        await saver_cm.__aexit__(None, None, None)


def main() -> int:
    parser = argparse.ArgumentParser(description="回填历史会话归属")
    parser.add_argument("--username", default=get_settings().admin_username)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    count = asyncio.run(backfill(args.username, args.dry_run))
    print(f"done: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
