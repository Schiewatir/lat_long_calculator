import pytest

from app.scoring.engine import DEFAULT_CONFIG, Signals, band_for, merged_config, score


def clean_postcode_row(**kw) -> Signals:
    base = dict(country_method="exact", country_to="FR", postcode_status="valid", postcode_found="found",
                postcode_to="75001", city_match="exact", city_from="Paris", city_to="Paris", precision="postcode")  # fmt: skip
    base.update(kw)
    return Signals(**base)


def test_clean_postcode_row_scores_100():
    r = score(clean_postcode_row())
    assert r.score == 100 and r.band == "High"
    assert r.reasons == [{"code": "exact", "penalty": 0, "text": "exact postcode and city match"}]


def test_corrupted_country_with_fuzzy_city():
    s = clean_postcode_row(country_method="inferred", country_from="GE", country_to="DE",
                           city_match="fuzzy", city_similarity=92, city_from="Muenchn", city_to="München")  # fmt: skip
    r = score(s)
    assert r.score == 74 and r.band == "Medium"
    texts = [(x["penalty"], x["text"]) for x in r.reasons]
    assert texts == [
        (15, "country GE→DE inferred from postcode and city"),
        (11, "city 'Muenchn' fuzzy-matched to 'München' (92%)"),
    ]


def test_city_level_cap():
    r = score(clean_postcode_row(precision="city"))
    assert r.score == 70
    assert r.reasons[0]["code"] == "cap_city"


def test_penalties_apply_below_the_cap():
    """An ambiguous city-level match ranks below an unambiguous one."""
    r = score(clean_postcode_row(precision="city", city_match="ambiguous"))
    assert r.score == 55 and r.band == "Low"
    assert sum(x["penalty"] for x in r.reasons) == 100 - r.score


def test_no_result_is_zero():
    r = score(Signals(precision="none"))
    assert r.score == 0 and r.band == "Failed"


def test_street_given_postcode_only_cap():
    assert score(clean_postcode_row(street_given=True)).score == 85


def test_country_level_cap():
    assert score(clean_postcode_row(precision="country")).score == 20


@pytest.mark.parametrize(("value", "band"), [(100, "High"), (90, "High"), (89, "Medium"), (74, "Medium"),
                                             (70, "Medium"), (69, "Low"), (40, "Low"), (39, "Failed"), (0, "Failed")])  # fmt: skip
def test_bands(value, band):
    assert band_for(value, DEFAULT_CONFIG) == band


def test_deterministic():
    s = clean_postcode_row(city_match="fuzzy", city_similarity=88.5, postcode_status="repaired")
    assert score(s) == score(s)


def test_distance_penalties():
    assert score(clean_postcode_row(pc_city_distance_km=20)).score == 85
    assert score(clean_postcode_row(pc_city_distance_km=80)).score == 65
    assert score(clean_postcode_row(pc_city_distance_km=5)).score == 100


def test_penalties_stack_and_clamp():
    s = clean_postcode_row(country_method="inferred", postcode_found="not_found", city_match="ambiguous",
                           ai_repaired=True, fields_fixed=True, pc_city_distance_km=200)  # fmt: skip
    r = score(s)
    assert r.score == 0
    assert [x["penalty"] for x in r.reasons] == sorted([x["penalty"] for x in r.reasons], reverse=True)


def test_manual_placement():
    r = score(Signals(manual_by="alice", precision="manual"))
    assert r.score == 100 and r.band == "High" and r.reasons[0]["text"] == "manually placed by alice"


def test_provider_signals():
    s = clean_postcode_row(precision="rooftop", provider="opencage", provider_confidence=3,
                           provider_postcode_mismatch=True)  # fmt: skip
    r = score(s)
    assert r.score == 70
    assert score(clean_postcode_row(street_given=True, country_method="alias")).score == 82


def test_config_override_changes_score():
    cfg = merged_config({"penalties": {"country_inferred": 30}})
    assert score(clean_postcode_row(country_method="inferred", country_from="GE", country_to="DE"), cfg).score == 70
    assert cfg["penalties"]["country_alias"] == 3
