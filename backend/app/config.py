"""Application settings, loaded from environment variables and the project `.env` file."""

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]
log = logging.getLogger(__name__)

# Google Maps Platform terms allow caching latitude/longitude for at most 30 consecutive days.
GOOGLE_MAX_CACHE_HOURS = 720


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT_DIR / ".env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # Infrastructure
    database_url: str = "postgresql+asyncpg://geo:geo@localhost:5432/geo"
    redis_url: str = "redis://localhost:6379/0"
    upload_dir: Path = ROOT_DIR / "uploads"
    geonames_dir: Path = ROOT_DIR / "data" / "geonames"

    # Auth
    session_idle_minutes: int = 480
    cookie_secure: bool = True
    login_max_failures: int = 10
    login_window_seconds: int = 900

    # OpenAI (input repair, column mapping, disambiguation only - never coordinates).
    # Either api.openai.com (OPENAI_API_KEY + OPENAI_MODEL) or an Azure-style gateway
    # (OPENAI_BASE_URL + OPENAI_CHAT_DEPLOYMENT + OPENAI_API_VERSION + OPENAI_AUTH_STYLE).
    openai_api_key: str | None = None
    openai_model: str = "gpt-5-mini"
    openai_base_url: str | None = None
    openai_api_version: str | None = None
    openai_chat_deployment: str | None = None
    openai_embedding_deployment: str | None = None  # accepted for compatibility; not used
    openai_auth_style: Literal["api-key", "bearer", "subscription-key"] = "bearer"
    openai_timeout_s: float = 60.0
    openai_temperature: float | None = None
    openai_rpm: int = 300
    openai_concurrency: int = 4
    openai_batch_enabled: bool | None = None  # default: on for api.openai.com, off for gateways
    ai_batch_threshold: int = 2000
    ai_addresses_per_request: int = 25

    # External geocoder
    geocoder_provider: str = "opencage"
    geocoder_api_key: str | None = None
    geocoder_rps: float = 1.0
    geocode_cache_ttl_days: int = 365
    postcode_mismatch_km: float = 25.0

    # Google Maps Platform: ONLY for the interactive distance calculator, never for jobs.
    # Results are never persisted to PostgreSQL or exported; Redis cache is capped at 30 days.
    google_maps_api_key: str | None = None
    google_routes_enabled: bool = True
    google_rps: float = 10.0
    google_cache_ttl_hours: int = 24
    google_language: str = "en"
    google_timeout_s: float = 10.0
    distance_per_user_per_min: int = 30
    distance_map: Literal["leaflet", "off"] = "off"

    # Limits
    max_upload_mb: int = 100
    max_rows: int = 250_000
    chunk_size: int = 500
    file_retention_days: int = 30

    # Frontend origin for CORS in development
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @field_validator("openai_base_url", "openai_api_version", "openai_chat_deployment",
                     "openai_embedding_deployment", "openai_api_key", "geocoder_api_key", "google_maps_api_key",
                     mode="before")
    @classmethod
    def _empty_is_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("google_cache_ttl_hours")
    @classmethod
    def _cap_google_cache(cls, v: int) -> int:
        if v > GOOGLE_MAX_CACHE_HOURS:
            log.warning("GOOGLE_CACHE_TTL_HOURS=%d exceeds the 30-day limit; using %d", v, GOOGLE_MAX_CACHE_HOURS)
            return GOOGLE_MAX_CACHE_HOURS
        return max(0, v)

    @model_validator(mode="after")
    def _no_google_for_jobs(self) -> "Settings":
        if self.geocoder_provider.strip().lower() == "google":
            raise ValueError(
                "GEOCODER_PROVIDER=google is not permitted: Google's terms forbid storing/exporting results. "
                "Google is only used by the distance calculator (GOOGLE_MAPS_API_KEY)."
            )
        return self

    @property
    def distance_address_available(self) -> bool:
        return bool(self.google_maps_api_key)

    @property
    def road_distance_available(self) -> bool:
        return bool(self.google_maps_api_key) and self.google_routes_enabled

    @property
    def ai_available(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def openai_gateway(self) -> bool:
        return bool(self.openai_base_url)

    @property
    def chat_model(self) -> str:
        return self.openai_chat_deployment or self.openai_model

    @property
    def ai_batch_supported(self) -> bool:
        """OpenAI Batch API; corporate gateways usually do not expose /files and /batches."""
        if self.openai_batch_enabled is not None:
            return self.openai_batch_enabled
        return not self.openai_gateway

    @property
    def provider_available(self) -> bool:
        return bool(self.geocoder_api_key) and self.geocoder_provider != "none"


@lru_cache
def get_settings() -> Settings:
    return Settings()
