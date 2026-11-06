import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, User


async def audit(
    db: AsyncSession,
    user: User | None,
    action: str,
    *,
    job_id: uuid.UUID | None = None,
    row_ids: list[int] | None = None,
    filter: dict | None = None,
    count: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Append an audit entry in the caller's transaction."""
    db.add(
        AuditLog(
            user_id=user.id if user else None,
            username=user.username if user else None,
            job_id=job_id,
            action=action,
            row_ids=row_ids,
            filter=filter,
            count=count,
            details=details or {},
        )
    )
