"""Agent 配置与管理员账号的数据访问层。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .models import AdminUser, AgentConfig, AgentStatus
from .schemas import AgentCreate, AgentUpdate
from .auth import hash_password

_settings = get_settings()


# ---------------------------------------------------------------------------
# AgentConfig CRUD
# ---------------------------------------------------------------------------
async def create_agent(session: AsyncSession, data: AgentCreate) -> AgentConfig:
    agent = AgentConfig(
        slug=data.slug,
        name=data.name,
        description=data.description,
        a2a_targets=[t.model_dump() for t in data.a2a_targets],
        system_prompt=data.system_prompt,
        enabled_tools=data.enabled_tools,
        status=AgentStatus.DRAFT,
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return agent


async def get_agent_by_slug(session: AsyncSession, slug: str) -> AgentConfig | None:
    result = await session.execute(select(AgentConfig).where(AgentConfig.slug == slug))
    return result.scalar_one_or_none()


async def get_agent_by_id(session: AsyncSession, agent_id: int) -> AgentConfig | None:
    return await session.get(AgentConfig, agent_id)


async def list_agents(session: AsyncSession) -> list[AgentConfig]:
    result = await session.execute(select(AgentConfig).order_by(AgentConfig.id))
    return list(result.scalars().all())


async def update_agent(
    session: AsyncSession, agent: AgentConfig, data: AgentUpdate
) -> AgentConfig:
    for field in (
        "name",
        "description",
        "a2a_targets",
        "system_prompt",
        "enabled_tools",
        "status",
    ):
        value = getattr(data, field)
        if value is not None:
            if field == "a2a_targets":
                agent.a2a_targets = [t.model_dump() for t in value]
            elif field == "status":
                agent.status = AgentStatus(value)
            else:
                setattr(agent, field, value)
    await session.commit()
    await session.refresh(agent)
    return agent


async def delete_agent(session: AsyncSession, agent: AgentConfig) -> None:
    await session.delete(agent)
    await session.commit()


async def set_agent_status(
    session: AsyncSession, agent: AgentConfig, status: AgentStatus
) -> AgentConfig:
    agent.status = status
    await session.commit()
    await session.refresh(agent)
    return agent


# ---------------------------------------------------------------------------
# 默认 Agent 初始化
# ---------------------------------------------------------------------------
async def ensure_default_agent(session: AsyncSession) -> AgentConfig:
    """应用启动时若不存在 slug='/' 的记录，自动创建，绑定 Hermes A2A 地址。"""
    existing = await get_agent_by_slug(session, "/")
    if existing is not None:
        return existing
    default = AgentConfig(
        slug="/",
        name="默认 Agent",
        description="绑定服务器 Hermes 的默认 Agent",
        a2a_targets=[
            {"url": _settings.hermes_a2a_url, "token": _settings.hermes_a2a_token}
        ],
        system_prompt=None,
        enabled_tools=[],
        status=AgentStatus.PUBLISHED,
    )
    session.add(default)
    await session.commit()
    await session.refresh(default)
    return default


# ---------------------------------------------------------------------------
# AdminUser
# ---------------------------------------------------------------------------
async def get_admin_by_username(
    session: AsyncSession, username: str
) -> AdminUser | None:
    result = await session.execute(select(AdminUser).where(AdminUser.username == username))
    return result.scalar_one_or_none()


async def ensure_default_admin(session: AsyncSession) -> AdminUser:
    """首次启动时若不存在配置的管理员账号，则自动创建。"""
    existing = await get_admin_by_username(session, _settings.admin_username)
    if existing is not None:
        return existing
    admin = AdminUser(
        username=_settings.admin_username,
        password_hash=hash_password(_settings.admin_password),
    )
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    return admin
