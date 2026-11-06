import httpx
import pytest
import respx

from app.geocode.base import ProviderError, ProviderQuery
from app.geocode.providers import OPENCAGE_URL, NullProvider, OpenCageProvider, build_provider
from app.geocode.router import MemoryGeocodeCache, geocode_normalized
from app.jobs.ratelimit import NoopLimiter
from app.normalize.pipeline import RawAddress, normalize
from app.scoring.engine import DEFAULT_CONFIG
from app.scoring.signals import score_candidate
from tests.fixtures import fixture_gazetteer

GAZ = fixture_gazetteer()


def oc_result(lat, lng, _type, conf=9, **comps):
    return {"geometry": {"lat": lat, "lng": lng}, "confidence": conf, "formatted": "x",
            "components": {"_type": _type, **comps}}  # fmt: skip


async def run(raw: RawAddress, provider=None, cache=None):
    n = await normalize(raw, GAZ)
    out = await geocode_normalized(n, provider or NullProvider(), cache or MemoryGeocodeCache(), NoopLimiter(),
                                   retry_base_delay=0)  # fmt: skip
    return n, out


async def test_postcode_row_stays_offline():
    provider = OpenCageProvider("k")
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(OPENCAGE_URL)
        n, out = await run(RawAddress(city="Paris", postcode="75001", country="FR"), provider)
    assert not route.called
    top = out.candidates[0]
    assert top.source == "geonames_postcode" and top.precision == "postcode"
    assert score_candidate(RawAddress(city="Paris", postcode="75001", country="FR"), n, top, [], DEFAULT_CONFIG).score == 100


@respx.mock
async def test_street_row_uses_provider_rooftop():
    respx.get(OPENCAGE_URL).mock(return_value=httpx.Response(200, json={"results": [
        oc_result(48.1374, 11.5755, "building", house_number="1", road="Marienplatz", postcode="80331")]}))
    raw = RawAddress(street="Marienplatz", house_number="1", city="München", postcode="80331", country="DE")
    n, out = await run(raw, OpenCageProvider("k"))
    top = out.candidates[0]
    assert top.source == "provider:opencage" and top.precision == "rooftop"
    assert out.provider_calls == 1
    req = respx.calls.last.request
    assert req.url.params["countrycode"] == "de" and req.url.params["q"] == "Marienplatz 1, 80331 München, Germany"
    # offline postcode result kept as an alternative
    assert any(c.source == "geonames_postcode" for c in out.candidates[1:])


@respx.mock
async def test_offline_miss_falls_back_to_provider():
    route = respx.get(OPENCAGE_URL).mock(return_value=httpx.Response(200, json={"results": [
        oc_result(48.2, 11.6, "village", conf=7)]}))
    n, out = await run(RawAddress(city="Kleinkleckersdorf", country="DE"), OpenCageProvider("k"))
    assert route.called and out.candidates[0].precision == "city"


@respx.mock
async def test_cache_hit_shared_across_jobs():
    route = respx.get(OPENCAGE_URL).mock(return_value=httpx.Response(200, json={"results": [
        oc_result(48.1374, 11.5755, "building", house_number="1")]}))
    cache = MemoryGeocodeCache()
    raw = RawAddress(street="Marienplatz 1", city="München", postcode="80331", country="DE")
    _, first = await run(raw, OpenCageProvider("k"), cache)
    _, second = await run(raw, OpenCageProvider("k"), cache)
    assert route.call_count == 1
    assert first.provider_calls == 1 and second.cache_hits == 1 and second.provider_calls == 0


@respx.mock
async def test_transient_error_retried():
    respx.get(OPENCAGE_URL).mock(side_effect=[
        httpx.Response(503), httpx.Response(200, json={"results": [oc_result(48.1374, 11.5755, "road")]})])
    _, out = await run(RawAddress(street="Marienplatz", city="München", postcode="80331", country="DE"),
                       OpenCageProvider("k"))  # fmt: skip
    assert out.error is None and out.candidates[0].precision == "street"


@respx.mock
async def test_persistent_errors_mark_geocode_error():
    respx.get(OPENCAGE_URL).mock(return_value=httpx.Response(500))
    _, out = await run(RawAddress(street="Marienplatz", city="München", postcode="80331", country="DE"),
                       OpenCageProvider("k"))  # fmt: skip
    assert out.error == "geocode_error"
    assert out.candidates[0].source == "geonames_postcode"  # offline fallback still available


@respx.mock
async def test_quota_exhausted_raises():
    respx.get(OPENCAGE_URL).mock(return_value=httpx.Response(402))
    with pytest.raises(ProviderError) as e:
        await run(RawAddress(street="Marienplatz", city="München", postcode="80331", country="DE"),
                  OpenCageProvider("k"))  # fmt: skip
    assert e.value.quota


@respx.mock
async def test_postcode_mismatch_flagged():
    respx.get(OPENCAGE_URL).mock(return_value=httpx.Response(200, json={"results": [
        oc_result(52.52, 13.40, "building", house_number="1")]}))  # Berlin, ~500 km from 80331
    raw = RawAddress(street="Hauptstraße 1", city="München", postcode="80331", country="DE")
    n, out = await run(raw, OpenCageProvider("k"))
    mismatched = [c for c in out.candidates if "postcode_mismatch" in c.flags]
    assert mismatched and out.candidates[0].source == "geonames_postcode"
    assert score_candidate(raw, n, mismatched[0], [], DEFAULT_CONFIG).score <= 80


async def test_no_provider_key_runs_offline_only():
    assert isinstance(build_provider("opencage", None), NullProvider)
    raw = RawAddress(street="Marienplatz 1", city="München", postcode="80331", country="DE")
    n, out = await run(raw)
    top = out.candidates[0]
    assert top.precision == "postcode"
    assert score_candidate(raw, n, top, [], DEFAULT_CONFIG).score == 85  # street given, postcode-level cap


async def test_ambiguous_city_produces_alternatives():
    _, out = await run(RawAddress(city="Frankfurt", country="DE"))
    assert len(out.candidates) == 2 and all("ambiguous" in c.flags for c in out.candidates)


async def test_unresolvable_row_has_no_candidates():
    raw = RawAddress(city="???")
    n, out = await run(raw)
    assert out.candidates == []
    r = score_candidate(raw, n, None, [], DEFAULT_CONFIG)
    assert r.score == 0 and r.band == "Failed"


async def test_conflict_shows_postcode_place():
    _, out = await run(RawAddress(city="Hamburg", postcode="80331", country="DE"))
    pc = next(c for c in out.candidates if c.source == "geonames_postcode")
    city = next(c for c in out.candidates if c.source == "geonames_city")
    assert pc.components["city"] == "München" and city.components["city"] == "Hamburg"


def test_query_cache_key_is_normalized():
    assert ProviderQuery(city="München ", country="DE").cache_key() == ProviderQuery(city="münchen", country="DE").cache_key()
