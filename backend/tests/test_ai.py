import json

from app.ai.repair import (
    REPAIR_SCHEMA,
    disambiguate,
    payload_item,
    repair_addresses,
    repair_request,
    validate_repair,
)
from app.geocode.base import GeoCandidate
from app.normalize.pipeline import RawAddress
from tests.fakes import FakeAI, echo_repair
from tests.fixtures import fixture_gazetteer

GAZ = fixture_gazetteer()


def test_schema_has_no_coordinate_fields():
    text = json.dumps(REPAIR_SCHEMA).lower()
    assert "lat" not in text.replace("translat", "") and "lon" not in text and "coord" not in text


def test_payload_contains_only_address_fields():
    item = payload_item("k1", RawAddress("Hauptstr.", "1", "Berlin", "10115", "DE"))
    assert set(item) == {"id", "street", "house_number", "city", "postcode", "country"}
    req = repair_request("x", [("k1", RawAddress(city="Berlin"))])
    assert set(json.loads(req.user)) == {"items"}


async def test_ai_repairs_garbled_row():
    original = RawAddress(city="Frnakfurt a.M.", postcode="6031", country="DEU.")
    ai = FakeAI(echo_repair({"Frnakfurt a.M.": {"city": "Frankfurt am Main", "postcode": "60311", "country": "DE",
                                                 "reason": "typo and truncated postcode"}}))  # fmt: skip
    reps = await repair_addresses(ai, [("k1", original)])
    n, reason = await validate_repair(original, reps["k1"], GAZ, None)
    assert n is not None and n.resolution == "resolved"
    assert "ai_repaired" in n.flags and n.city == "Frankfurt am Main" and n.postcode == "60311"
    ai_fields = {c.field for c in n.corrections if c.tier == "ai"}
    assert ai_fields == {"city", "postcode", "country"}
    assert reason == "typo and truncated postcode"


async def test_ai_hallucinated_postcode_rejected():
    original = RawAddress(city="Muenchn", postcode="99999", country="DE")
    ai = FakeAI(echo_repair({"Muenchn": {"city": "München", "postcode": "80999"}}))
    reps = await repair_addresses(ai, [("k1", original)])
    n, reason = await validate_repair(original, reps["k1"], GAZ, None)
    assert n is None and reason.startswith("AI suggestion failed validation")
    assert "80999" in reason


async def test_ai_no_change_not_accepted():
    original = RawAddress(city="Nowhere", country="DE")
    ai = FakeAI(echo_repair({}))
    reps = await repair_addresses(ai, [("k1", original)])
    n, _ = await validate_repair(original, reps["k1"], GAZ, None)
    assert n is None


async def test_unknown_ids_ignored():
    ai = FakeAI(lambda req: {"items": [{"id": "evil", "street": "", "house_number": "", "city": "x", "postcode": "",
                                        "country": "DE", "changed_fields": [], "reason": ""}]})  # fmt: skip
    assert await repair_addresses(ai, [("k1", RawAddress(city="a"))]) == {}


async def test_disambiguation_picks_candidate_by_id():
    cands = [GeoCandidate(50.1, 8.7, "geonames_city", "city", "Frankfurt am Main"),
             GeoCandidate(52.3, 14.5, "geonames_city", "city", "Frankfurt (Oder)")]  # fmt: skip
    ai = FakeAI(lambda req: {"choice": "c2", "reason": "Oder region street name"})
    idx, reason = await disambiguate(ai, RawAddress(street="Oderstraße 1", city="Frankfurt", country="DE"), cands)
    assert idx == 1 and "Oder" in reason
    payload = json.loads(ai.requests[0].user)
    assert "lat" not in json.dumps(payload["candidates"])


async def test_disambiguation_invalid_choice_ignored():
    cands = [GeoCandidate(50.1, 8.7, "geonames_city", "city", "A")]
    ai = FakeAI(lambda req: {"choice": "c7", "reason": "?"})
    idx, _ = await disambiguate(ai, RawAddress(city="A"), cands)
    assert idx is None
