import pytest

from app.normalize.country import resolve_country
from app.normalize.pipeline import RawAddress, normalize
from app.normalize.postcode import repair_postcode
from app.normalize.text import clean
from tests.fixtures import fixture_gazetteer

GAZ = fixture_gazetteer()


async def norm(city="", postcode="", country="", street="", default=None):
    return await normalize(RawAddress(street=street, city=city, postcode=postcode, country=country), GAZ, default)


# --- T0 cleanup


def test_whitespace_and_control_characters():
    assert clean("  Berlin\t ") == "Berlin"
    assert clean("Ber\x00lin") == "Ber lin"


# --- country resolution


@pytest.mark.parametrize(
    ("raw", "code", "method"),
    [
        ("DEU", "DE", "alias"),
        ("UK", "GB", "alias"),
        ("Germnay", "DE", "fuzzy"),
        ("de", "DE", "exact"),
        ("Deutschland", "DE", "alias"),
        ("U.S.A.", "US", "alias"),
        ("Holland", "NL", "alias"),
        ("GE", "GE", "exact"),
    ],
)
def test_country_resolution(raw, code, method):
    res = resolve_country(raw)
    assert (res.code, res.method) == (code, method)


def test_fuzzy_country_records_score():
    assert resolve_country("Germnay").score and resolve_country("Germnay").score >= 85


# --- cross-field country validation


async def test_corrupted_country_code_overridden():
    r = await norm("Munchen", "80331", "GE")
    assert r.country == "DE" and r.country_method == "inferred"
    assert r.country_overridden_from == "GE"
    c = next(c for c in r.corrections if c.field == "country")
    assert c.from_ == "GE" and c.to == "DE" and "80331" in c.reason
    assert r.city == "München" and r.resolution == "resolved"


async def test_consistent_country_not_overridden():
    r = await norm("Tbilisi", "0105", "GE")
    assert r.country == "GE" and r.country_method == "exact"
    assert r.postcode_found == "unverifiable"
    assert r.resolution == "resolved"


async def test_missing_country_inferred():
    r = await norm("Paris", "75001", "")
    assert r.country == "FR" and r.country_method == "inferred"
    assert r.resolution == "resolved"


async def test_postcode_alone_in_two_countries_is_not_inferred():
    r = await norm("", "75001", "")
    assert r.country is None and r.resolution == "unresolved"


async def test_job_default_country():
    r = await norm("Berlin", "10115", "", default="DE")
    assert r.country == "DE" and r.country_method == "job_default"
    assert any(c.tier == "job_default" for c in r.corrections)


# --- postcode repair


async def test_leading_zero_restored():
    r = await norm("Dresden", "1067", "DE")
    assert r.postcode == "01067" and r.postcode_status == "repaired"
    assert any("zero-padded 1067→01067" in c.reason for c in r.corrections)
    assert r.postcode_found == "found"


def test_excel_float_artifact():
    assert repair_postcode("80331.0", "DE").value == "80331"


async def test_invalid_postcode_flagged():
    r = await norm("München", "ABCDE", "DE")
    assert r.postcode_status == "invalid_format" and "postcode_invalid_format" in r.flags
    assert r.city == "München" and r.resolution == "resolved"


@pytest.mark.parametrize(
    ("raw", "country", "expected"),
    [("sw1a1aa", "GB", "SW1A 1AA"), ("1234ab", "NL", "1234 AB"), ("D-80331", "DE", "80331"),
     ("02134-1234", "US", "02134"), ("6000", "FR", "06000"), ("2134", "US", "02134")],
)  # fmt: skip
def test_postcode_formats(raw, country, expected):
    assert repair_postcode(raw, country).value == expected


# --- city matching


async def test_exonym_alias():
    r = await norm("Munich", "", "DE")
    assert r.city == "München" and r.city_match == "alias"


async def test_typo_fuzzy():
    r = await norm("Muenchn", "", "DE")
    assert r.city == "München" and r.city_match == "fuzzy"
    assert r.city_similarity and r.city_similarity >= 85


async def test_ambiguous_city_resolved_by_postcode():
    r = await norm("Frankfurt", "15230", "DE")
    assert r.city == "Frankfurt (Oder)" and r.resolution == "resolved"


async def test_ambiguous_city_without_postcode():
    r = await norm("Frankfurt", "", "DE")
    assert r.city_match == "ambiguous" and r.resolution == "ambiguous"
    names = {c["name"] for c in r.city_candidates}
    assert names == {"Frankfurt am Main", "Frankfurt (Oder)"}


async def test_abbreviation_expanded():
    r = await norm("St. Augustin", "", "DE")
    assert r.city == "Sankt Augustin"


async def test_city_filled_from_postcode():
    r = await norm("", "80331", "DE")
    assert r.city == "München" and r.city_match == "from_postcode" and r.resolution == "resolved"


async def test_postcode_city_conflict_unresolved():
    r = await norm("Hamburg", "80331", "DE")
    assert "postcode_city_conflict" in r.flags and r.resolution == "unresolved"
    assert r.pc_city_distance_km and r.pc_city_distance_km > 50


# --- field split / swap


async def test_combined_postcode_and_city():
    r = await norm("80331 München", "", "DE")
    assert r.postcode == "80331" and r.city == "München" and r.fields_fixed
    assert any(c.field == "postcode" and c.to == "80331" for c in r.corrections)


async def test_swapped_fields():
    r = await norm("80331", "München", "DE")
    assert r.postcode == "80331" and r.city == "München" and r.fields_fixed


# --- resolution and correction log


async def test_clean_row_resolved_without_corrections():
    r = await norm("Paris", "75001", "FR")
    assert r.resolution == "resolved" and r.corrections == []


async def test_multiple_corrections_in_order():
    r = await norm("Dresden", "1067", "DEU")
    assert [c.field for c in r.corrections] == ["country", "postcode"]
    assert all(c.sentence for c in r.corrections)


async def test_unknown_country_unresolved():
    r = await norm("???", "", "")
    assert r.resolution == "unresolved" and "country_unknown" in r.flags
