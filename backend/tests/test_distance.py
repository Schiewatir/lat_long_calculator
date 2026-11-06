import json
import logging

import httpx
import pytest
import respx
from pydantic import ValidationError
from sqlalchemy import text

from app.config import Settings, get_settings
from app.distance.google import GEOCODE_URL, ROUTES_URL, GoogleGeocoder, GoogleRoutes, google_precision
from app.distance.models import AddressInput, CoordsInput, DistanceRequest
from app.distance.service import AddressLookupUnavailable, DistanceService, LocationError
from app.geocode.base import ProviderError
from app.geocode.providers import build_provider
from app.jobs.ratelimit import NoopLimiter, RateLimiter
from tests.conftest import login, make_user

BERLIN = {"type": "coords", "lat": 52.5200, "lon": 13.4050}
MUNICH = {"type": "coords", "lat": 48.1374, "lon": 11.5755}


def g_result(label, lat, lon, location_type="APPROXIMATE", types=("locality", "political"), **kw):
    return {"formatted_address": label, "geometry": {"location": {"lat": lat, "lng": lon},
            "location_type": location_type}, "types": list(types), "place_id": f"pid-{label}", **kw}  # fmt: skip


def g_ok(*results):
    return httpx.Response(200, json={"status": "OK", "results": list(results)})


MARIENPLATZ = g_result("Marienplatz 1, 80331 München, Germany", 48.1372, 11.5755, "ROOFTOP", ("street_address",))
ROUTE_OK = httpx.Response(200, json={"routes": [{"distanceMeters": 584700, "duration": "19800s",
                                                 "polyline": {"encodedPolyline": "abc"}}]})  # fmt: skip


class MemCache:
    def __init__(self):
        self.data: dict[str, str] = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value


def service(http: httpx.AsyncClient | None = None, *, routes: bool = True, ttl: int = 3600) -> DistanceService:
    http = http or httpx.AsyncClient()
    return DistanceService(
        GoogleGeocoder("test-key", http), GoogleRoutes("test-key", http) if routes else None,
        MemCache(), NoopLimiter(), cache_ttl_s=ttl, retry_base_delay=0,
    )  # fmt: skip


def req(origin, destination, road=True) -> DistanceRequest:
    return DistanceRequest.model_validate({"origin": origin, "destination": destination, "road": road})


# --- 1. configuration rules -------------------------------------------------------------------------


def test_google_rejected_as_job_provider():
    with pytest.raises(ValidationError, match="not permitted"):
        Settings(geocoder_provider="google")
    with pytest.raises(ValueError):
        build_provider("google", "some-key")


def test_google_cache_ttl_clamped(caplog):
    with caplog.at_level(logging.WARNING):
        s = Settings(google_cache_ttl_hours=60 * 24)
    assert s.google_cache_ttl_hours == 720
    assert "30-day" in caplog.text


def test_capability_properties():
    assert not Settings(google_maps_api_key="").distance_address_available
    s = Settings(google_maps_api_key="k", google_routes_enabled=False)
    assert s.distance_address_available and not s.road_distance_available


# --- 2. input models --------------------------------------------------------------------------------


def test_coordinate_ranges_name_the_field():
    with pytest.raises(ValidationError) as e:
        req({"type": "coords", "lat": 95.0, "lon": 0}, MUNICH)
    assert any(err["loc"][-1] == "lat" and err["loc"][0] == "origin" for err in e.value.errors())
    with pytest.raises(ValidationError):
        CoordsInput(lat=0, lon=-181)
    with pytest.raises(ValidationError):
        CoordsInput(lat=float("nan"), lon=0)


def test_empty_address_rejected():
    with pytest.raises(ValidationError, match="at least one"):
        AddressInput(street="  ", city="")


@pytest.mark.parametrize("raw", ["Deutschland", "DEU", "de", "Germany"])
def test_country_normalized(raw):
    assert AddressInput(city="Berlin", country=raw).country == "DE"


def test_unknown_country_rejected():
    with pytest.raises(ValidationError, match="unknown country"):
        AddressInput(city="Somewhere", country="Atlantis")


# --- 3. Google geocoding client ---------------------------------------------------------------------


def test_precision_mapping():
    assert google_precision(MARIENPLATZ) == "rooftop"
    assert google_precision(g_result("x", 0, 0, "RANGE_INTERPOLATED", ("street_address",))) == "street"
    assert google_precision(g_result("x", 0, 0, "APPROXIMATE", ("postal_code",))) == "postcode"
    assert google_precision(g_result("x", 0, 0, "APPROXIMATE", ("locality", "political"))) == "city"
    assert google_precision(g_result("x", 0, 0, "APPROXIMATE", ("administrative_area_level_1",))) == "region"
    assert google_precision(g_result("x", 0, 0, "APPROXIMATE", ("country", "political"))) == "country"
    assert google_precision(g_result("x", 0, 0, "APPROXIMATE", ("establishment",))) == "region"


@respx.mock
async def test_structured_address_request_and_parse():
    route = respx.get(GEOCODE_URL).mock(return_value=g_ok(MARIENPLATZ, g_result("München", 48.13, 11.58)))
    places = await GoogleGeocoder("test-key", httpx.AsyncClient()).geocode(
        AddressInput(street="Marienplatz", house_number="1", postcode="80331", city="München", country="Germany")
    )
    params = route.calls.last.request.url.params
    assert params["address"] == "Marienplatz 1"
    assert params["components"] == "country:DE|postal_code:80331|locality:München"
    assert params["language"] == "en"
    assert places[0].precision == "rooftop" and places[0].place_id.startswith("pid-")
    assert len(places) == 2


@respx.mock
async def test_components_only_query():
    route = respx.get(GEOCODE_URL).mock(return_value=g_ok(g_result("80331 Munich", 48.1, 11.5, types=("postal_code",))))
    await GoogleGeocoder("k", httpx.AsyncClient()).geocode(AddressInput(postcode="80331", country="DE"))
    params = route.calls.last.request.url.params
    assert "address" not in params and params["components"] == "country:DE|postal_code:80331"


@respx.mock
async def test_zero_results_and_denied():
    respx.get(GEOCODE_URL).mock(return_value=httpx.Response(200, json={"status": "ZERO_RESULTS", "results": []}))
    assert await GoogleGeocoder("k", httpx.AsyncClient()).geocode(AddressInput(city="Nowhere")) == []
    respx.get(GEOCODE_URL).mock(
        return_value=httpx.Response(200, json={"status": "REQUEST_DENIED", "error_message": "API key invalid"})
    )
    with pytest.raises(ProviderError) as e:
        await GoogleGeocoder("k", httpx.AsyncClient()).geocode(AddressInput(city="X"))
    assert e.value.quota


@respx.mock
async def test_transient_error_retried():
    respx.get(GEOCODE_URL).mock(side_effect=[httpx.Response(503), g_ok(MARIENPLATZ)])
    r = await service().calculate(req({"type": "address", "text": "Marienplatz 1, München"}, BERLIN, road=False))
    assert r.origin.label.startswith("Marienplatz") and r.origin.source == "google"


# --- 4. Google routes client ------------------------------------------------------------------------


@respx.mock
async def test_route_parsed_with_field_mask():
    call = respx.post(ROUTES_URL).mock(return_value=ROUTE_OK)
    route = await GoogleRoutes("test-key", httpx.AsyncClient()).compute((52.52, 13.405), (48.1374, 11.5755))
    assert route.distance_m == 584700 and route.duration_s == 19800 and route.polyline == "abc"
    sent = call.calls.last.request
    assert sent.headers["X-Goog-FieldMask"] == "routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline"
    assert json.loads(sent.content)["travelMode"] == "DRIVE"


@respx.mock
async def test_no_route_and_route_failure():
    respx.post(ROUTES_URL).mock(return_value=httpx.Response(200, json={}))
    r = await service().calculate(req(BERLIN, {"type": "coords", "lat": 40.71, "lon": -74.0}))
    assert r.road is None and r.road_distance_unavailable == "no_route"
    respx.post(ROUTES_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    r = await service().calculate(req(BERLIN, MUNICH))
    assert r.road is None and r.road_distance_unavailable == "provider_error"
    assert 503 < r.straight_line.km < 505


# --- 5. rate limiter + service ----------------------------------------------------------------------


async def test_try_acquire_rejects_over_limit(db):
    from app.redis import get_redis

    lim = RateLimiter.per_window(get_redis(), "test:user", 2, 60)
    assert await lim.try_acquire() is None
    assert await lim.try_acquire() is None
    retry = await lim.try_acquire()
    assert retry is not None and 0 < retry <= 60


@respx.mock
async def test_berlin_munich_coords_only():
    geo = respx.get(GEOCODE_URL)
    respx.post(ROUTES_URL).mock(return_value=ROUTE_OK)
    r = await service().calculate(req(BERLIN, MUNICH))
    assert not geo.called
    assert abs(r.straight_line.km - 504) <= 1 and abs(r.straight_line.mi - r.straight_line.km / 1.609344) < 0.01
    assert r.road.km == 584.7 and r.road.duration_s == 19800
    assert r.origin.source == "input" and r.origin.precision == "manual"
    assert r.attribution  # route came from Google


@respx.mock
async def test_mixed_modes_one_geocode_call():
    geo = respx.get(GEOCODE_URL).mock(return_value=g_ok(g_result("80331 Munich", 48.14, 11.57, types=("postal_code",))))
    r = await service(routes=False).calculate(req({"type": "address", "postcode": "80331", "country": "DE"}, BERLIN))
    assert geo.call_count == 1
    assert r.origin.precision == "postcode" and r.destination.source == "input"
    assert r.road_distance_unavailable == "disabled"


@respx.mock
async def test_cache_hit_avoids_second_call():
    geo = respx.get(GEOCODE_URL).mock(return_value=g_ok(MARIENPLATZ))
    svc = service(routes=False)
    addr = {"type": "address", "street": "Marienplatz 1", "city": "München", "country": "DE"}
    await svc.calculate(req(addr, BERLIN))
    await svc.calculate(req({**addr, "city": "  München "}, MUNICH))
    assert geo.call_count == 1


@respx.mock
async def test_same_point_skips_route():
    routes = respx.post(ROUTES_URL)
    r = await service().calculate(req(BERLIN, BERLIN))
    assert r.straight_line.km == 0 and r.road_distance_unavailable == "same_point" and not routes.called


@respx.mock
async def test_alternatives_and_picked_alternative():
    respx.get(GEOCODE_URL).mock(return_value=g_ok(
        g_result("Frankfurt am Main, Germany", 50.11, 8.68, partial_match=True),
        g_result("Frankfurt (Oder), Germany", 52.34, 14.55),
    ))  # fmt: skip
    r = await service(routes=False).calculate(req(BERLIN, {"type": "address", "city": "Frankfurt"}))
    assert r.destination.partial_match and r.destination.alternatives[0].label.startswith("Frankfurt (Oder)")
    alt = r.destination.alternatives[0]
    picked = {"type": "coords", "lat": alt.lat, "lon": alt.lon, "label": alt.label, "precision": alt.precision,
              "place_id": alt.place_id}  # fmt: skip
    r2 = await service(routes=False).calculate(req(BERLIN, picked))
    assert r2.destination.label == alt.label and r2.destination.source == "google"
    assert r2.straight_line.km < r.straight_line.km


@respx.mock
async def test_not_found_side_keeps_other_side():
    respx.get(GEOCODE_URL).mock(return_value=httpx.Response(200, json={"status": "ZERO_RESULTS", "results": []}))
    with pytest.raises(LocationError) as e:
        await service().calculate(req(BERLIN, {"type": "address", "city": "Qwxzyv"}))
    assert e.value.side == "destination" and "origin" in e.value.resolved


async def test_no_key_address_unavailable_coords_ok():
    svc = DistanceService(None, None, MemCache(), NoopLimiter(), cache_ttl_s=0)
    with pytest.raises(AddressLookupUnavailable) as e:
        await svc.calculate(req({"type": "address", "city": "Lyon", "country": "FR"}, BERLIN))
    assert not e.value.configured
    r = await svc.calculate(req(BERLIN, MUNICH))
    assert r.road_distance_unavailable == "disabled" and r.attribution == []


# --- 6. API -----------------------------------------------------------------------------------------


@pytest.fixture
def google_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "google_maps_api_key", "test-key")


async def _signed_in(client, db):
    await make_user(db, "dist")
    await login(client, "dist")


async def test_api_requires_auth(client):
    with respx.mock(assert_all_called=False) as mock:
        geo = mock.get(GEOCODE_URL)
        r = await client.post("/api/distance", json={"origin": BERLIN, "destination": MUNICH})
    assert r.status_code == 401 and not geo.called


async def test_api_validation_and_capabilities(client, db):
    await _signed_in(client, db)
    r = await client.post("/api/distance", json={"origin": {"type": "coords", "lat": 95, "lon": 0}, "destination": MUNICH})
    assert r.status_code == 422 and "lat" in r.text
    r = await client.post("/api/distance", json={"origin": {"type": "address", "country": "Atlantis"}, "destination": MUNICH})
    assert r.status_code == 422 and "unknown country" in r.text
    caps = (await client.get("/api/capabilities")).json()
    assert caps["distance_address_available"] is False and caps["road_distance_available"] is False
    assert caps["distance_map"] == "off"


async def test_api_no_key(client, db):
    await _signed_in(client, db)
    r = await client.post("/api/distance", json={"origin": BERLIN, "destination": MUNICH})
    assert r.status_code == 200 and abs(r.json()["straight_line"]["km"] - 504) <= 1
    r = await client.post("/api/distance", json={"origin": {"type": "address", "city": "Lyon"}, "destination": MUNICH})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "address_lookup_not_configured"


async def test_api_rate_limited(client, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "distance_per_user_per_min", 2)
    await _signed_in(client, db)
    for _ in range(2):
        assert (await client.post("/api/distance", json={"origin": BERLIN, "destination": MUNICH})).status_code == 200
    r = await client.post("/api/distance", json={"origin": BERLIN, "destination": MUNICH})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


async def test_api_request_denied_is_generic_503(client, db, google_key, caplog):
    await _signed_in(client, db)
    with respx.mock(assert_all_called=False) as mock, caplog.at_level(logging.INFO):
        mock.get(GEOCODE_URL).mock(
            return_value=httpx.Response(200, json={"status": "REQUEST_DENIED", "error_message": "The provided API key is invalid."})
        )
        r = await client.post("/api/distance", json={"origin": {"type": "address", "city": "Lyon"}, "destination": MUNICH})
    assert r.status_code == 503
    assert r.json()["detail"]["message"] == "Address lookup temporarily unavailable"
    assert "invalid" not in r.text
    assert "The provided API key is invalid." in caplog.text and "test-key" not in caplog.text


async def test_api_not_found_side(client, db, google_key):
    await _signed_in(client, db)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(GEOCODE_URL).mock(return_value=httpx.Response(200, json={"status": "ZERO_RESULTS", "results": []}))
        r = await client.post("/api/distance", json={"origin": BERLIN, "destination": {"type": "address", "city": "Qwxzyv"}})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["side"] == "destination" and detail["resolved"]["origin"]["lat"] == 52.52


async def _row_counts(db) -> dict[str, int]:
    tables = (await db.execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
    ))).scalars().all()  # fmt: skip
    return {t: (await db.execute(text(f'SELECT count(*) FROM "{t}"'))).scalar_one() for t in tables}


async def test_google_results_not_persisted(client, db, google_key):
    await _signed_in(client, db)
    before = await _row_counts(db)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(GEOCODE_URL).mock(return_value=g_ok(MARIENPLATZ))
        mock.post(ROUTES_URL).mock(return_value=ROUTE_OK)
        r = await client.post("/api/distance", json={
            "origin": {"type": "address", "street": "Marienplatz 1", "city": "München", "country": "DE"},
            "destination": BERLIN,
        })  # fmt: skip
    assert r.status_code == 200 and r.json()["road"]["km"] == 584.7
    await db.rollback()
    after = await _row_counts(db)
    # The session row's last_seen may be touched, but no table gains rows - geocode_cache in particular.
    assert after == before
    assert after["geocode_cache"] == 0
