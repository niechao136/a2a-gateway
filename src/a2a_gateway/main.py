"""FastAPI 应用入口。

启动时：
- 创建数据库表（开发期用 create_all；生产用 Alembic）
- 初始化默认 Agent（slug=/ 绑定 Hermes）
- 初始化默认管理员账号
关停时关闭 Agent 工厂缓存的连接。
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .agent_factory import close_all
from .config import get_settings
from .database import async_engine
from .migrations import run_migrations
from .models import Base
from .repository import (
    ensure_all_agent_api_keys,
    ensure_default_admin,
    ensure_default_agent,
)
from .routes import a2a_server, admin, chat, connectors, registry, speech
from .database import AsyncSessionLocal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
_settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：执行 Alembic 迁移（历史库会自动 stamp 基线接管）
    try:
        await asyncio.to_thread(run_migrations)
    except Exception:
        logger.exception("Alembic 迁移失败，回退到 create_all（仅建表，不做版本管理）")
        async with async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库表已就绪")
    async with AsyncSessionLocal() as session:
        await ensure_default_agent(session)
        await ensure_default_admin(session)
        # 为每个尚无 Key 的 Agent 补齐默认 API Key（含历史 Agent 升级）
        await ensure_all_agent_api_keys(session)
    logger.info("默认 Agent、管理员账号与 API Key 已就绪")
    yield
    # 关停
    await close_all()
    logger.info("Agent 工厂缓存已关闭")


app = FastAPI(
    title="a2a-gateway",
    description="基于 LangGraph 的多 Agent 平台",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """统一兜底错误处理，避免暴露底层异常。"""
    logger.exception("未捕获异常 %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试"},
    )


app.include_router(chat.router)
app.include_router(admin.router)
app.include_router(registry.router)
app.include_router(a2a_server.router)
app.include_router(speech.router)
app.include_router(connectors.admin_router)
app.include_router(connectors.webhook_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


def main():
    """本地开发启动入口。"""
    import uvicorn

    uvicorn.run(
        "a2a_gateway.main:app",
        host=_settings.app_host,
        port=_settings.app_port,
        reload=True,
    )


if __name__ == "__main__":
    main()
