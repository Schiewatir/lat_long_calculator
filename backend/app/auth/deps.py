from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_token
from app.config import get_settings
from app.db import get_session
from app.models import Session, User

SESSION_COOKIE = "gw_session"
CSRF_COOKIE = "gw_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Endpoints a user with a forced password change may still call.
PASSWORD_CHANGE_ALLOWED = {"/api/auth/me", "/api/auth/logout", "/api/auth/change-password"}


@dataclass
class AuthContext:
    user: User
    session: Session

    @property
    def is_admin(self) -> bool:
        return self.user.role == "admin"


async def load_auth(request: Request, db: AsyncSession) -> AuthContext:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "Not authenticated")
    sess = await db.scalar(select(Session).where(Session.token_hash == hash_token(token)))
    now = datetime.now(UTC)
    if sess is None or sess.expires_at <= now:
        raise HTTPException(401, "Session expired")
    user = await db.get(User, sess.user_id)
    if user is None or not user.active:
        raise HTTPException(401, "Not authenticated")
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != sess.csrf_token:
        raise HTTPException(403, "CSRF token missing or invalid")
    # Sliding idle expiry; written at most once a minute to keep request overhead low.
    if now - sess.last_seen > timedelta(seconds=60):
        sess.last_seen = now
        sess.expires_at = now + timedelta(minutes=get_settings().session_idle_minutes)
        await db.commit()
    return AuthContext(user=user, session=sess)


async def current_auth(request: Request, db: AsyncSession = Depends(get_session)) -> AuthContext:
    auth = await load_auth(request, db)
    if auth.user.must_change_password and request.url.path not in PASSWORD_CHANGE_ALLOWED:
        raise HTTPException(403, {"code": "password_change_required", "message": "Please set a new password."})
    return auth


async def require_admin(auth: AuthContext = Depends(current_auth)) -> AuthContext:
    if not auth.is_admin:
        raise HTTPException(403, "Admin role required")
    return auth
