"""共享依赖：管理员 JWT 认证。"""

import uuid

from fastapi import Depends, Header, HTTPException, status

from .auth import decode_access_token
from .database import get_session
from .repository import get_admin_by_username
from .models import AdminUser
from sqlalchemy.ext.asyncio import AsyncSession


async def get_current_admin(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> AdminUser:
    """从 Authorization: Bearer <token> 解析管理员身份。"""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少有效的认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    username = decode_access_token(token)
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    admin = await get_admin_by_username(session, username)
    if admin is None or admin.disabled:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号不存在或已禁用",
        )
    return admin


def new_thread_id() -> str:
    """生成匿名访客 session id。"""
    return str(uuid.uuid4())
