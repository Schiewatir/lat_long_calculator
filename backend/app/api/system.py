from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import AuthContext, current_auth
from app.config import get_settings
from app.db import get_session
from app.models import GazetteerMeta

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/capabilities")
async def capabilities(_: AuthContext = Depends(current_auth), session: AsyncSession = Depends(get_session)) -> dict:
    """What this deployment can do; drives UI notices (no secrets are returned)."""
    settings = get_settings()
    meta = {m.key: m.value for m in (await session.scalars(select(GazetteerMeta))).all()}
    return {
        "ai_available": settings.ai_available,
        "ai_model": settings.chat_model if settings.ai_available else None,
        "street_geocoding_available": settings.provider_available,
        "geocoder_provider": settings.geocoder_provider if settings.provider_available else None,
        "gazetteer_imported_at": meta.get("imported_at"),
        "gazetteer_countries": int(meta.get("postal_countries", "0")),
        "max_upload_mb": settings.max_upload_mb,
        "max_rows": settings.max_rows,
        "distance_address_available": settings.distance_address_available,
        "road_distance_available": settings.road_distance_available,
        "distance_map": settings.distance_map,
        "attribution": [
            "Gazetteer data © GeoNames (geonames.org), CC BY 4.0",
            *(["Geocoding by OpenCage (opencagedata.com), data © OpenStreetMap contributors, ODbL"]
              if settings.provider_available and settings.geocoder_provider == "opencage" else []),
            *(["Distance calculator: geocoding and routing by Google"] if settings.distance_address_available else []),
        ],
    }
