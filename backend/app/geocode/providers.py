"""External geocoding providers for jobs. Only providers whose terms allow storing and exporting results
belong here. Google is deliberately not a job provider; it is used only by the interactive distance
calculator (`app.distance`), whose results are never persisted."""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

import httpx

from app.geocode.base import GeoCandidate, GeocoderProvider, ProviderError, ProviderQuery
from app.normalize.country import country_name

log = logging.getLogger(__name__)

OPENCAGE_URL = "https://api.opencagedata.com/geocode/v1/json"
_CITY_TYPES = {"city", "town", "village", "hamlet", "municipality", "neighbourhood", "suburb", "city_district",
               "locality", "quarter", "place"}  # fmt: skip
_REGION_TYPES = {"county", "state", "state_district", "region", "island", "archipelago"}


class NullProvider:
    """Used when no geocoder key is configured: street-level geocoding is unavailable."""

    name = "none"

    async def geocode(self, query: ProviderQuery) -> list[GeoCandidate]:
        return []


def opencage_precision(components: dict, confidence: float | None) -> str:
    kind = components.get("_type", "")
    if kind in ("building", "house") or (kind == "road" and components.get("house_number")):
        return "rooftop" if components.get("house_number") else "street"
    if kind in ("road", "street", "pedestrian"):
        return "street"
    if kind in ("postcode",):
        return "postcode"
    if kind in _CITY_TYPES:
        return "city"
    if kind in _REGION_TYPES:
        return "region"
    if kind == "country":
        return "country"
    # Unknown feature types (POIs etc.): fall back on OpenCage's bounding-box confidence.
    if confidence is not None and confidence >= 9:
        return "street"
    if confidence is not None and confidence >= 6:
        return "city"
    return "region"


class OpenCageProvider:
    name = "opencage"

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None, timeout: float = 15.0):
        self.api_key = api_key
        self.client = client or httpx.AsyncClient(timeout=timeout)

    async def geocode(self, query: ProviderQuery) -> list[GeoCandidate]:
        params = {
            "q": query.text(country_name(query.country)),
            "key": self.api_key,
            "limit": 5,
            "no_annotations": 1,
            "no_record": 1,
        }
        if query.country:
            params["countrycode"] = query.country.lower()
        try:
            resp = await self.client.get(OPENCAGE_URL, params=params)
        except httpx.TimeoutException as e:
            raise ProviderError("geocoder timeout", transient=True) from e
        except httpx.TransportError as e:
            raise ProviderError(f"geocoder connection error: {e}", transient=True) from e
        if resp.status_code == 402:
            raise ProviderError("geocoder quota exhausted", quota=True)
        if resp.status_code in (401, 403):
            raise ProviderError("geocoder API key invalid or disabled", quota=True)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise ProviderError(f"geocoder HTTP {resp.status_code}", transient=True)
        if resp.status_code != 200:
            raise ProviderError(f"geocoder HTTP {resp.status_code}")
        out = []
        for res in resp.json().get("results", []):
            comps = res.get("components", {})
            conf = res.get("confidence")
            out.append(
                GeoCandidate(
                    lat=float(res["geometry"]["lat"]),
                    lon=float(res["geometry"]["lng"]),
                    source=f"provider:{self.name}",
                    precision=opencage_precision(comps, conf),
                    label=res.get("formatted", ""),
                    components={k: v for k, v in comps.items() if isinstance(v, str | int | float)},
                    provider_confidence=float(conf) if conf is not None else None,
                )
            )
        return out


def build_provider(name: str, api_key: str | None) -> GeocoderProvider:
    if not api_key or name in ("", "none"):
        return NullProvider()
    if name == "opencage":
        return OpenCageProvider(api_key)
    raise ValueError(f"Unknown or unsupported geocoder provider: {name}")


async def with_retries[T](call: Callable[[], Awaitable[T]], attempts: int = 5, base_delay: float = 0.5) -> T:
    """Retry transient provider failures with exponential backoff and jitter."""
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except ProviderError as e:
            if not e.transient or attempt == attempts:
                raise
            delay = base_delay * 2 ** (attempt - 1) * (1 + random.random() * 0.25)
            log.warning("geocoder transient error (%s), retry %d in %.1fs", e, attempt, delay)
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")
