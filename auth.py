import os
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Header, HTTPException
from jose import JWTError, jwt
from passlib.context import CryptContext

_SECRET = os.environ.get("JWT_SECRET") or secrets.token_hex(32)
_ALGORITHM = "HS256"
_ACCESS_EXPIRE_MINUTES = int(os.environ.get("JWT_ACCESS_EXPIRE_MINUTES", "60"))
_REFRESH_EXPIRE_DAYS   = int(os.environ.get("JWT_REFRESH_EXPIRE_DAYS",   "7"))

if not os.environ.get("JWT_SECRET"):
    import logging
    logging.getLogger("auth").warning(
        "JWT_SECRET not set — using ephemeral secret. "
        "All tokens will be invalidated on server restart. "
        "Set JWT_SECRET in .env for production."
    )

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(payload: dict) -> str:
    data = dict(payload)
    data["exp"] = datetime.utcnow() + timedelta(minutes=_ACCESS_EXPIRE_MINUTES)
    return jwt.encode(data, _SECRET, algorithm=_ALGORITHM)


def create_refresh_token(user_id: int) -> tuple:
    """Returns (token_str, token_hash, expires_at_str)."""
    token = secrets.token_hex(48)
    token_hash = pwd_context.hash(token)
    expires_at = datetime.utcnow() + timedelta(days=_REFRESH_EXPIRE_DAYS)
    return token, token_hash, expires_at.isoformat()


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, _SECRET, algorithms=[_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return decode_token(authorization[7:])


def require_role(*roles):
    from fastapi import Depends

    async def _dep(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user

    return _dep


require_teacher = require_role("teacher", "super_admin")
require_admin   = require_role("super_admin")


_USERNAME_CHARS = string.ascii_lowercase + string.digits


def generate_temp_credentials() -> tuple:
    """Returns (username, plain_password, hashed_password)."""
    username = "".join(secrets.choice(_USERNAME_CHARS) for _ in range(8))
    password = secrets.token_urlsafe(12)
    return username, password, hash_password(password)


def generate_access_code() -> str:
    return secrets.token_hex(4)
