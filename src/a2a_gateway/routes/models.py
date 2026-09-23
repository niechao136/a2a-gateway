"""模型注册表路由：CRUD + 连通性测试。

所有接口均位于 /api/admin 下，需要 JWT 管理员认证。
删除保护语义与 A2A/MCP/Skill 注册表一致：被 Agent 引用时默认拒绝（409），
`?force=true` 强制删除并自动解绑。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from .. import repository as repo
from ..agent_factory import invalidate_agent
from ..database import get_session
from ..deps import get_current_admin
from ..llm_probe import probe_llm
from ..models import AdminUser, LLMModel
from ..schemas import (
    LLMModelCreate,
    LLMModelOut,
    LLMModelUpdate,
    mask_secret,
    validate_llm_model,
)

router = APIRouter(prefix="/api/admin", tags=["admin-registry"])


def _used_by_message(agents) -> str:
    names = "、".join(a.name for a in agents)
    return f"仍被 {len(agents)} 个 Agent 引用（{names}）；可先取消绑定，或用 ?force=true 强制删除并自动解绑"


def _out(m: LLMModel) -> dict:
    provider = m.provider.value if hasattr(m.provider, "value") else str(m.provider)
    return {
        "id": m.id,
        "name": m.name,
        "provider": provider,
        "base_url": m.base_url,
        "model": m.model,
        "description": m.description,
        "api_key_masked": mask_secret(m.api_key or ""),
        "created_at": m.created_at,
        "updated_at": m.updated_at,
    }


@router.get("/models", response_model=list[LLMModelOut])
async def list_models(
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    return [_out(m) for m in await repo.list_llm_models(session)]


@router.post("/models", response_model=LLMModelOut, status_code=status.HTTP_201_CREATED)
async def create_model(
    data: LLMModelCreate,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    if not data.name.strip():
        raise HTTPException(400, "名称不能为空")
    try:
        validate_llm_model(data.provider, data.base_url, data.model)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if await repo.get_llm_model_by_name(session, data.name.strip()) is not None:
        raise HTTPException(409, f"名称 '{data.name}' 已存在")
    return _out(await repo.create_llm_model(session, data))


@router.put("/models/{model_id}", response_model=LLMModelOut)
async def update_model(
    model_id: int,
    data: LLMModelUpdate,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    m = await repo.get_llm_model(session, model_id)
    if m is None:
        raise HTTPException(404, "模型不存在")

    # 合并后按 provider 校验必填项（与 create 一致）
    provider = data.provider or (
        m.provider.value if hasattr(m.provider, "value") else str(m.provider)
    )
    base_url = data.base_url if data.base_url is not None else m.base_url
    model_name = data.model if data.model is not None else m.model
    try:
        validate_llm_model(provider, base_url or "", model_name or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    new_name = data.name.strip() if data.name else m.name
    if new_name != m.name:
        dup = await repo.get_llm_model_by_name(session, new_name)
        if dup is not None:
            raise HTTPException(409, f"名称 '{new_name}' 已存在")

    updated = await repo.update_llm_model(session, m, data)
    # 配置变更 → 刷新引用方快照并失效图缓存（Agent 无需重启即生效）
    for agent in await repo.refresh_agents_for_model(session, model_id):
        await invalidate_agent(agent.id)
    return _out(updated)


@router.delete("/models/{model_id}", status_code=204)
async def delete_model(
    model_id: int,
    force: bool = Query(False, description="为 true 时自动从所有 Agent 解绑后删除"),
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    m = await repo.get_llm_model(session, model_id)
    if m is None:
        raise HTTPException(404, "模型不存在")
    used = await repo.agents_using_model(session, model_id)
    if used and not force:
        raise HTTPException(409, _used_by_message(used))
    if used:
        for agent in used:
            await invalidate_agent(agent.id)
        await repo.detach_model_from_agents(session, model_id)
    await repo.delete_llm_model(session, m)


@router.post("/models/{model_id}/test")
async def test_model_by_id(
    model_id: int,
    session: AsyncSession = Depends(get_session),
    _: AdminUser = Depends(get_current_admin),
):
    """测试已保存模型的连通性（发一条极短消息验证三元组）。"""
    m = await repo.get_llm_model(session, model_id)
    if m is None:
        raise HTTPException(404, "模型不存在")
    provider = m.provider.value if hasattr(m.provider, "value") else str(m.provider)
    ok, message = await probe_llm(provider, m.base_url, m.api_key, m.model)
    return {"ok": ok, "message": message}


@router.post("/models/test")
async def test_model_form(
    data: LLMModelCreate,
    _: AdminUser = Depends(get_current_admin),
):
    """未保存表单直测：请求体复用 LLMModelCreate（api_key 为表单明文）。"""
    try:
        validate_llm_model(data.provider, data.base_url, data.model)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    ok, message = await probe_llm(data.provider, data.base_url, data.api_key, data.model)
    return {"ok": ok, "message": message}
