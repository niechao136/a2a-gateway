"""管理员认证：JWT 颁发 + 密码哈希。

密码哈希直接使用 bcrypt（不再依赖已停止维护的 passlib——passlib 1.7.4 的
bcrypt 后端自检在 bcrypt 4.1+/5.x 下会抛 ValueError，导致启动失败）。
"""

from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from .config import get_settings

_settings = get_settings()

# bcrypt 算法只使用密码的前 72 字节，超出部分需显式截断（bcrypt 5.x 会直接报错）
_BCRYPT_MAX_BYTES = 72


def _to_bcrypt_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_to_bcrypt_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_to_bcrypt_bytes(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=_settings.jwt_expire_minutes)
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, _settings.jwt_secret, algorithm=_settings.jwt_algorithm)


def decode_access_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, _settings.jwt_secret, algorithms=[_settings.jwt_algorithm])
        return payload.get("sub")
    except JWTError:
        return None
