"""ORM models. Results live on per-job `AddressKey`s (one per unique normalized address) and are
denormalized onto `Row`s so that grid filtering, sorting and export are single-table queries."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _now() -> Any:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# --------------------------------------------------------------------------- auth


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _now()

    __table_args__ = (Index("uq_users_username_lower", func.lower(username), unique=True),)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = _now()
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --------------------------------------------------------------------------- jobs


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # draft | queued | ingesting | normalizing | ai_repair | geocoding | scoring | ready
    # | paused | failed | cancelled
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    stage_cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    options: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str | None] = mapped_column(Text)
    file_meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    mapping: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    counts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    scoring_config_version: Mapped[int | None] = mapped_column(Integer)
    ai_batch_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    file_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AddressKey(Base):
    __tablename__ = "address_keys"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)  # 0-based order within job, drives chunking
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # cleaned input (T0)
    in_street: Mapped[str] = mapped_column(Text, nullable=False, default="")
    in_house_number: Mapped[str] = mapped_column(Text, nullable=False, default="")
    in_city: Mapped[str] = mapped_column(Text, nullable=False, default="")
    in_postcode: Mapped[str] = mapped_column(Text, nullable=False, default="")
    in_country: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # normalized
    n_street: Mapped[str | None] = mapped_column(Text)
    n_house_number: Mapped[str | None] = mapped_column(Text)
    n_city: Mapped[str | None] = mapped_column(Text)
    n_postcode: Mapped[str | None] = mapped_column(Text)
    n_country: Mapped[str | None] = mapped_column(String(2))
    resolution: Mapped[str | None] = mapped_column(String(16))  # resolved | unresolved | ambiguous
    norm_state: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # serialized NormResult
    corrections: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    flags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    ai_reason: Mapped[str | None] = mapped_column(Text)
    # result
    selected_candidate_id: Mapped[int | None] = mapped_column(BigInteger)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(String(40))
    precision: Mapped[str | None] = mapped_column(String(16))
    m_street: Mapped[str | None] = mapped_column(Text)
    m_city: Mapped[str | None] = mapped_column(Text)
    m_postcode: Mapped[str | None] = mapped_column(Text)
    m_country: Mapped[str | None] = mapped_column(String(2))
    score: Mapped[int | None] = mapped_column(Integer)
    band: Mapped[str | None] = mapped_column(String(8))
    reasons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(String(32))  # geocode_error | ai_error
    # pipeline progress: normalized -> ai_done -> geocoded -> scored
    step: Mapped[str] = mapped_column(String(16), nullable=False, default="new")

    __table_args__ = (
        UniqueConstraint("job_id", "key_hash", name="uq_address_keys_job_key"),
        Index("ix_address_keys_job_seq", "job_id", "seq"),
        Index("ix_address_keys_job_step", "job_id", "step"),
    )


class Row(Base):
    __tablename__ = "rows"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based data row number
    raw: Mapped[list] = mapped_column(JSONB, nullable=False)  # all original values, in column order
    in_street: Mapped[str] = mapped_column(Text, nullable=False, default="")
    in_house_number: Mapped[str] = mapped_column(Text, nullable=False, default="")
    in_city: Mapped[str] = mapped_column(Text, nullable=False, default="")
    in_postcode: Mapped[str] = mapped_column(Text, nullable=False, default="")
    in_country: Mapped[str] = mapped_column(Text, nullable=False, default="")
    address_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("address_keys.id", ondelete="SET NULL"), index=True
    )
    # effective result (copied from the address key unless overridden on this row)
    overridden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    override: Mapped[dict | None] = mapped_column(JSONB)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(String(40))
    precision: Mapped[str | None] = mapped_column(String(16))
    m_street: Mapped[str | None] = mapped_column(Text)
    m_city: Mapped[str | None] = mapped_column(Text)
    m_postcode: Mapped[str | None] = mapped_column(Text)
    m_country: Mapped[str | None] = mapped_column(String(2))
    score: Mapped[int | None] = mapped_column(Integer)
    band: Mapped[str | None] = mapped_column(String(8))
    flags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(String(32))
    # review
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    search: Mapped[str] = mapped_column(
        Text,
        Computed(
            "lower(in_street || ' ' || in_city || ' ' || in_postcode || ' ' || in_country || ' ' || "
            "coalesce(m_street, '') || ' ' || coalesce(m_city, '') || ' ' || coalesce(m_postcode, ''))",
            persisted=True,
        ),
    )

    __table_args__ = (
        UniqueConstraint("job_id", "row_index", name="uq_rows_job_index"),
        Index("ix_rows_job_score", "job_id", "score", "row_index"),
        Index("ix_rows_job_status", "job_id", "status"),
        Index("ix_rows_job_band", "job_id", "band"),
        Index("ix_rows_flags", "flags", postgresql_using="gin"),
        Index("ix_rows_search_trgm", "search", postgresql_using="gin", postgresql_ops={"search": "gin_trgm_ops"}),
    )


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    address_key_id: Mapped[int] = mapped_column(
        ForeignKey("address_keys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    precision: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False, default="")
    components: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    provider_confidence: Mapped[float | None] = mapped_column(Float)
    flags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    score: Mapped[int | None] = mapped_column(Integer)
    band: Mapped[str | None] = mapped_column(String(8))
    reasons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)


class GeocodeCache(Base):
    __tablename__ = "geocode_cache"
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    query_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    query: Mapped[dict] = mapped_column(JSONB, nullable=False)
    response: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _now()


class ScoringConfig(Base):
    __tablename__ = "scoring_config"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _now()
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = _now()
    user_id: Mapped[int | None] = mapped_column(Integer)
    username: Mapped[str | None] = mapped_column(String(64))
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)  # no FK: survives deletion
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    row_ids: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger))
    filter: Mapped[dict | None] = mapped_column(JSONB)
    count: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (Index("ix_audit_row_ids", "row_ids", postgresql_using="gin"),)


# --------------------------------------------------------------------------- gazetteer


class GeonamesPostal(Base):
    __tablename__ = "geonames_postal"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    postcode: Mapped[str] = mapped_column(String(20), nullable=False)
    pc_key: Mapped[str] = mapped_column(String(20), nullable=False)  # compact uppercase, no separators
    place_name: Mapped[str] = mapped_column(Text, nullable=False)
    admin1: Mapped[str | None] = mapped_column(Text)
    admin2: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    accuracy: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_postal_country_pckey", "country", "pc_key"),
        Index("ix_postal_pckey", "pc_key"),
    )


class GeonamesPlace(Base):
    __tablename__ = "geonames_place"
    geonameid: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    asciiname: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    feature_code: Mapped[str | None] = mapped_column(String(10))
    admin1: Mapped[str | None] = mapped_column(String(20))
    population: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)


class GazetteerName(Base):
    """Unified, accent-folded name index over place names, alternate names (exonyms, local
    spellings) and postal place names; drives exact and fuzzy city matching."""

    __tablename__ = "gazetteer_names"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    name_folded: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)  # display / canonical name of the place
    geonameid: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # place | alt | postal
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    population: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        Index("ix_gnames_country_folded", "country", "name_folded"),
        Index("ix_gnames_folded", "name_folded"),
    )


class GazetteerMeta(Base):
    __tablename__ = "gazetteer_meta"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


__all__ = [
    "AddressKey",
    "AuditLog",
    "Candidate",
    "GazetteerMeta",
    "GazetteerName",
    "GeocodeCache",
    "GeonamesPlace",
    "GeonamesPostal",
    "Job",
    "Row",
    "ScoringConfig",
    "Session",
    "User",
]
