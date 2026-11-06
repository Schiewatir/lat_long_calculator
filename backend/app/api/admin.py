from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import AuthContext, require_admin
from app.auth.security import hash_password, password_policy_error
from app.db import get_session
from app.models import Session, User
from app.review.audit import audit

router = APIRouter(prefix="/admin/users", tags=["admin"])

Role = Literal["user", "admin"]


class UserCreate(BaseModel):
    username: str
    password: str
    role: Role = "user"


class UserUpdate(BaseModel):
    role: Role | None = None
    active: bool | None = None


class PasswordReset(BaseModel):
    password: str


def _out(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "role": u.role,
        "active": u.active,
        "must_change_password": u.must_change_password,
        "created_at": u.created_at.isoformat(),
    }


async def _get(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    return user


async def _active_admins(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).where(User.role == "admin", User.active.is_(True))) or 0


@router.get("")
async def list_users(_: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    return [_out(u) for u in (await db.scalars(select(User).order_by(User.username))).all()]


@router.post("", status_code=201)
async def create_user(body: UserCreate, auth: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    username = body.username.strip()
    if not username or len(username) > 64:
        raise HTTPException(422, "Username must be 1-64 characters.")
    if error := password_policy_error(body.password):
        raise HTTPException(422, error)
    if await db.scalar(select(User.id).where(func.lower(User.username) == username.lower())):
        raise HTTPException(409, "Username already exists")
    user = User(username=username, password_hash=hash_password(body.password), role=body.role)
    db.add(user)
    await db.flush()
    await audit(db, auth.user, "user_create", details={"user_id": user.id, "username": username, "role": body.role})
    await db.commit()
    return _out(user)


@router.patch("/{user_id}")
async def update_user(
    user_id: int, body: UserUpdate, auth: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_session)
):
    user = await _get(db, user_id)
    loses_admin = user.role == "admin" and user.active and (body.role == "user" or body.active is False)
    if loses_admin and await _active_admins(db) <= 1:
        raise HTTPException(409, "Cannot deactivate or demote the last active admin")
    changes = {}
    if body.role is not None and body.role != user.role:
        changes["role"] = [user.role, body.role]
        user.role = body.role
    if body.active is not None and body.active != user.active:
        changes["active"] = [user.active, body.active]
        user.active = body.active
        if not body.active:
            await db.execute(delete(Session).where(Session.user_id == user.id))
    if changes:
        await audit(db, auth.user, "user_update", details={"user_id": user.id, "changes": changes})
    await db.commit()
    return _out(user)


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: int, body: PasswordReset, auth: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_session)
):
    user = await _get(db, user_id)
    if error := password_policy_error(body.password):
        raise HTTPException(422, error)
    user.password_hash = hash_password(body.password)
    user.must_change_password = True
    await db.execute(delete(Session).where(Session.user_id == user.id))
    await audit(db, auth.user, "user_password_reset", details={"user_id": user.id})
    await db.commit()
    return _out(user)


# --------------------------------------------------------------------------- scoring configuration

config_router = APIRouter(prefix="/admin/scoring-config", tags=["admin"])


class ScoringConfigIn(BaseModel):
    config: dict


@config_router.get("")
async def get_scoring_config(_: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    from app.jobs.context import active_scoring_config
    from app.models import ScoringConfig

    version, config = await active_scoring_config(db)
    versions = (await db.scalars(select(ScoringConfig).order_by(ScoringConfig.version.desc()))).all()
    return {"active_version": version, "config": config,
            "versions": [{"version": v.version, "active": v.active, "created_at": v.created_at.isoformat()}
                         for v in versions]}  # fmt: skip


@config_router.post("", status_code=201)
async def new_scoring_config(body: ScoringConfigIn, auth: AuthContext = Depends(require_admin),
                             db: AsyncSession = Depends(get_session)):  # fmt: skip
    """Create a new active version. Existing jobs keep their scores until explicitly re-scored."""
    from sqlalchemy import update

    from app.jobs.context import active_scoring_config
    from app.models import ScoringConfig
    from app.scoring.engine import DEFAULT_CONFIG, merged_config

    unknown = set(body.config) - set(DEFAULT_CONFIG)
    if unknown:
        raise HTTPException(422, f"Unknown sections: {', '.join(sorted(unknown))}")
    for section, values in body.config.items():
        if isinstance(DEFAULT_CONFIG[section], dict):
            bad = set(values) - set(DEFAULT_CONFIG[section]) if isinstance(values, dict) else {"<not an object>"}
            if bad and section != "provider_low_confidence_below":
                raise HTTPException(422, f"Unknown keys in {section}: {', '.join(sorted(bad))}")
    await active_scoring_config(db)  # make sure version 1 exists
    latest = await db.scalar(select(func.max(ScoringConfig.version))) or 0
    await db.execute(update(ScoringConfig).values(active=False))
    row = ScoringConfig(version=latest + 1, config=merged_config(body.config), active=True, created_by=auth.user.id)
    db.add(row)
    await audit(db, auth.user, "scoring_config_create", details={"version": row.version})
    await db.commit()
    return {"active_version": row.version, "config": row.config}
