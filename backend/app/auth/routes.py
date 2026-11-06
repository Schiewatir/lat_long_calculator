from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import CSRF_COOKIE, SESSION_COOKIE, AuthContext, current_auth
from app.auth.security import hash_password, hash_token, new_token, password_policy_error, verify_password
from app.config import get_settings
from app.db import get_session
from app.models import Session, User
from app.redis import get_redis

router = APIRouter(prefix="/auth", tags=["auth"])

IP_FAILURE_MULTIPLIER = 5  # an IP may be shared by several users (office NAT)


class LoginIn(BaseModel):
    username: str
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


def user_out(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "must_change_password": user.must_change_password,
    }


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def _limited(keys: list[tuple[str, int]]) -> bool:
    redis = get_redis()
    for key, limit in keys:
        value = await redis.get(key)
        if value is not None and int(value) >= limit:
            return True
    return False


async def _record_failure(keys: list[str]) -> None:
    redis = get_redis()
    window = get_settings().login_window_seconds
    for key in keys:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window)


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_session)):
    settings = get_settings()
    user_key = f"login_fail:user:{body.username.strip().lower()}"
    ip_key = f"login_fail:ip:{_client_ip(request)}"
    max_fail = settings.login_max_failures
    if await _limited([(user_key, max_fail), (ip_key, max_fail * IP_FAILURE_MULTIPLIER)]):
        raise HTTPException(429, "Too many failed login attempts. Try again later.")

    user = await db.scalar(select(User).where(func.lower(User.username) == body.username.strip().lower()))
    ok = verify_password(user.password_hash if user else None, body.password)
    if not ok or user is None or not user.active:
        await _record_failure([user_key, ip_key])
        raise HTTPException(401, "Invalid username or password")

    await get_redis().delete(user_key)
    token, csrf = new_token(), new_token()
    now = datetime.now(UTC)
    db.add(
        Session(
            token_hash=hash_token(token),
            csrf_token=csrf,
            user_id=user.id,
            last_seen=now,
            expires_at=now + timedelta(minutes=settings.session_idle_minutes),
        )
    )
    await db.commit()
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, secure=settings.cookie_secure, samesite="lax", path="/"
    )
    # Double-submit CSRF token: readable by the frontend, echoed back in the X-CSRF-Token header.
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=settings.cookie_secure, samesite="lax", path="/")
    return user_out(user)


@router.post("/logout")
async def logout(response: Response, auth: AuthContext = Depends(current_auth), db: AsyncSession = Depends(get_session)):
    await db.execute(delete(Session).where(Session.id == auth.session.id))
    await db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(auth: AuthContext = Depends(current_auth)):
    return user_out(auth.user)


@router.post("/change-password")
async def change_password(
    body: ChangePasswordIn, auth: AuthContext = Depends(current_auth), db: AsyncSession = Depends(get_session)
):
    if not verify_password(auth.user.password_hash, body.current_password):
        raise HTTPException(400, "Current password is incorrect")
    if error := password_policy_error(body.new_password):
        raise HTTPException(422, error)
    if body.new_password == body.current_password:
        raise HTTPException(422, "New password must differ from the current one.")
    user = await db.get(User, auth.user.id)
    assert user is not None
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    # Sign out other sessions of this user.
    await db.execute(delete(Session).where(Session.user_id == user.id, Session.id != auth.session.id))
    await db.commit()
    return user_out(user)
