"""Google Geocoding API and Routes API clients (distance calculator only - never used by jobs)."""

import contextlib
import logging
from dataclasses import dataclass

import httpx

from app.distance.models import AddressInput, Precision
from app.geocode.base import ProviderError

log = logging.getLogger(__name__)

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
ROUTES_FIELD_MASK = "routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline"
MAX_RESULTS = 5  # top match + up to 4 alternatives

_STREET_TYPES = {"street_address", "route", "premise", "subpremise", "intersection", "street_number"}
_POSTCODE_TYPES = {"postal_code", "postal_code_prefix"}
_CITY_TYPES = {"locality", "sublocality", "postal_town", "neighborhood", "sublocality_level_1",
               "sublocality_level_2", "administrative_area_level_3", "administrative_area_level_4"}  # fmt: skip
_REGION_TYPES = {"administrative_area_level_1", "administrative_area_level_2", "administrative_area_level_5",
                 "colloquial_area", "natural_feature"}  # fmt: skip
_QUOTA_STATUSES = {"OVER_QUERY_LIMIT", "OVER_DAILY_LIMIT", "REQUEST_DENIED"}


class InvalidQuery(Exception):
    """Google rejected the query itself (INVALID_REQUEST); not retryable, reported as 422."""


@dataclass
class Place:
    label: str
    lat: float
    lon: float
    precision: Precision
    place_id: str | None = None
    partial_match: bool = False


@dataclass
class Route:
    distance_m: float
    duration_s: int
    polyline: str | None


def google_precision(result: dict) -> Precision:
    location_type = result.get("geometry", {}).get("location_type")
    types = set(result.get("types", []))
    if location_type == "ROOFTOP":
        return "rooftop"
    if location_type == "RANGE_INTERPOLATED" or types & _STREET_TYPES:
        return "street"
    if types & _POSTCODE_TYPES:
        return "postcode"
    if types & _CITY_TYPES:
        return "city"
    if "country" in types:
        return "country"
    return "region"  # administrative areas and anything unrecognized


def geocode_params(a: AddressInput) -> dict[str, str]:
    """Structured parts become hard `components` filters; street/free text go in `address`."""
    street = " ".join(x for x in (a.street, a.house_number) if x)
    address = ", ".join(x for x in (street, a.text) if x)
    comps = [f"{k}:{v.replace('|', ' ').replace(':', ' ')}" for k, v in (
        ("country", a.country), ("postal_code", a.postcode), ("locality", a.city), ("administrative_area", a.region),
    ) if v]  # fmt: skip
    params: dict[str, str] = {}
    if address:
        params["address"] = address
    if comps:
        params["components"] = "|".join(comps)
    return params


def _transport_error(e: httpx.HTTPError, what: str) -> ProviderError:
    if isinstance(e, httpx.TimeoutException):
        return ProviderError(f"{what} timeout", transient=True)
    return ProviderError(f"{what} connection error: {type(e).__name__}", transient=True)


class GoogleGeocoder:
    def __init__(self, api_key: str, client: httpx.AsyncClient, language: str = "en"):
        self.api_key = api_key
        self.client = client
        self.language = language

    async def geocode(self, a: AddressInput) -> list[Place]:
        params = {**geocode_params(a), "language": self.language, "key": self.api_key}
        try:
            resp = await self.client.get(GEOCODE_URL, params=params)
        except httpx.HTTPError as e:
            raise _transport_error(e, "Google geocoding") from e
        if resp.status_code == 429 or resp.status_code >= 500:
            raise ProviderError(f"Google geocoding HTTP {resp.status_code}", transient=True)
        if resp.status_code != 200:
            raise ProviderError(f"Google geocoding HTTP {resp.status_code}", quota=resp.status_code in (401, 403))
        body = resp.json()
        status = body.get("status")
        if status == "ZERO_RESULTS":
            return []
        if status in _QUOTA_STATUSES:
            # error_message never contains the key; logged server-side only.
            log.error("Google geocoding %s: %s", status, body.get("error_message", ""))
            raise ProviderError(f"Google geocoding {status}", quota=True)
        if status == "INVALID_REQUEST":
            raise InvalidQuery(body.get("error_message") or "invalid address query")
        if status != "OK":
            raise ProviderError(f"Google geocoding {status}", transient=True)
        out = []
        for r in body.get("results", [])[:MAX_RESULTS]:
            loc = r["geometry"]["location"]
            out.append(Place(label=r.get("formatted_address", ""), lat=float(loc["lat"]), lon=float(loc["lng"]),
                             precision=google_precision(r), place_id=r.get("place_id"),
                             partial_match=bool(r.get("partial_match"))))  # fmt: skip
        return out


def _duration_s(value: str | None) -> int:
    """Routes API durations are protobuf strings like '19800s' or '12.5s'."""
    if not value:
        return 0
    return round(float(value.rstrip("s")))


class GoogleRoutes:
    def __init__(self, api_key: str, client: httpx.AsyncClient, language: str = "en"):
        self.api_key = api_key
        self.client = client
        self.language = language

    async def compute(self, o: tuple[float, float], d: tuple[float, float]) -> Route | None:
        """Driving route, or None when no drivable route exists."""
        body = {
            "origin": {"location": {"latLng": {"latitude": o[0], "longitude": o[1]}}},
            "destination": {"location": {"latLng": {"latitude": d[0], "longitude": d[1]}}},
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_UNAWARE",
            "languageCode": self.language,
            "units": "METRIC",
        }
        headers = {"X-Goog-Api-Key": self.api_key, "X-Goog-FieldMask": ROUTES_FIELD_MASK}
        try:
            resp = await self.client.post(ROUTES_URL, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise _transport_error(e, "Google routes") from e
        if resp.status_code == 429 or resp.status_code >= 500:
            raise ProviderError(f"Google routes HTTP {resp.status_code}", transient=True)
        if resp.status_code != 200:
            message = ""
            with contextlib.suppress(ValueError):
                message = resp.json().get("error", {}).get("message", "")
            log.error("Google routes HTTP %d: %s", resp.status_code, message)
            raise ProviderError(f"Google routes HTTP {resp.status_code}", quota=resp.status_code in (401, 403))
        routes = resp.json().get("routes") or []
        if not routes:
            return None
        r = routes[0]
        return Route(distance_m=float(r.get("distanceMeters", 0)), duration_s=_duration_s(r.get("duration")),
                     polyline=(r.get("polyline") or {}).get("encodedPolyline"))  # fmt: skip
