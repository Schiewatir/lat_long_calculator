"""Deterministic, explainable confidence score (0-100).

`score()` is a pure function of measurable `Signals` and a versioned config: the same input
always yields the same score. LLM self-assessments are never an input."""

import copy
import math
from dataclasses import dataclass, field

DEFAULT_CONFIG: dict = {
    "penalties": {
        "country_alias": 3,
        "country_fuzzy": 8,
        "country_inferred": 15,
        "country_job_default": 2,
        "postcode_repaired": 5,
        "postcode_not_found": 25,  # invalid format or not in gazetteer
        "postcode_partial": 5,  # only a postcode prefix was given (e.g. GB outward code)
        "city_alias": 0,
        "city_fuzzy_base": 5,
        "city_fuzzy_per_point": 0.75,
        "city_fuzzy_max": 20,
        "city_ambiguous": 15,
        "city_not_found": 10,  # city given but unknown; location from postcode only
        "fields_fixed": 5,
        "ai_repaired": 10,
        "ai_disambiguated": 10,
        "distance_medium": 15,
        "distance_far": 35,
        "provider_postcode_mismatch": 20,
        "provider_low_confidence": 10,
    },
    "distance_km": {"medium": 10, "far": 50},
    "caps": {"city": 70, "region": 40, "country": 20, "none": 0, "street_given_postcode_only": 85},
    "bands": {"high": 90, "medium": 70, "low": 40},
    # Provider-native confidence below this counts as low (OpenCage: 1-10, bounding-box based).
    "provider_low_confidence_below": {"opencage": 5},
}

BANDS = ("High", "Medium", "Low", "Failed")


@dataclass
class Signals:
    country_method: str | None = None
    country_from: str = ""
    country_to: str | None = None
    country_score: float | None = None
    postcode_status: str = "missing"
    postcode_found: str = "n/a"
    postcode_partial: bool = False
    postcode_from: str = ""
    postcode_to: str | None = None
    city_match: str = "missing"
    city_similarity: float | None = None
    city_from: str = ""
    city_to: str | None = None
    fields_fixed: bool = False
    ai_repaired: bool = False
    ai_disambiguated: bool = False
    pc_city_distance_km: float | None = None
    precision: str = "none"
    street_given: bool = False
    provider: str | None = None
    provider_confidence: float | None = None
    provider_postcode_mismatch: bool = False
    manual_by: str | None = None


@dataclass
class ScoreResult:
    score: int
    band: str
    reasons: list[dict] = field(default_factory=list)


def round_half_up(x: float) -> int:
    return math.floor(x + 0.5)


def band_for(score: int, config: dict) -> str:
    b = config["bands"]
    if score >= b["high"]:
        return "High"
    if score >= b["medium"]:
        return "Medium"
    if score >= b["low"]:
        return "Low"
    return "Failed"


def merged_config(overrides: dict | None) -> dict:
    """Default config deep-merged with a stored (possibly partial) config."""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    for section, values in (overrides or {}).items():
        if isinstance(values, dict) and isinstance(cfg.get(section), dict):
            cfg[section].update(values)
        else:
            cfg[section] = values
    return cfg


def score(s: Signals, config: dict | None = None) -> ScoreResult:
    cfg = config or DEFAULT_CONFIG
    p = cfg["penalties"]

    if s.manual_by:
        return ScoreResult(100, band_for(100, cfg), [{"code": "manual", "penalty": 0,
                                                      "text": f"manually placed by {s.manual_by}"}])  # fmt: skip
    if s.precision == "none":
        return ScoreResult(0, band_for(0, cfg), [{"code": "no_location", "penalty": 100, "text": "no location found"}])

    reasons: list[dict] = []

    def pen(code: str, amount: float, text: str) -> None:
        if amount > 0:
            reasons.append({"code": code, "penalty": int(amount), "text": text})

    # Country
    if s.country_method == "alias":
        pen("country_alias", p["country_alias"], f"country '{s.country_from}' resolved to {s.country_to}")
    elif s.country_method == "fuzzy":
        pen("country_fuzzy", p["country_fuzzy"],
            f"country '{s.country_from}' fuzzy-matched to {s.country_to} ({s.country_score or 0:.0f}%)")  # fmt: skip
    elif s.country_method == "inferred":
        frm = f"{s.country_from}→" if s.country_from else ""
        pen("country_inferred", p["country_inferred"], f"country {frm}{s.country_to} inferred from postcode and city")
    elif s.country_method == "job_default":
        pen("country_job_default", p["country_job_default"], f"country {s.country_to} taken from job default")

    # Postcode
    if s.postcode_status == "invalid_format":
        pen("postcode_not_found", p["postcode_not_found"], f"postcode '{s.postcode_from}' has an invalid format")
    elif s.postcode_found == "not_found":
        pen("postcode_not_found", p["postcode_not_found"], f"postcode {s.postcode_to} not found in {s.country_to}")
    elif s.postcode_status == "repaired":
        pen("postcode_repaired", p["postcode_repaired"], f"postcode repaired '{s.postcode_from}'→'{s.postcode_to}'")
    if s.postcode_partial:
        pen("postcode_partial", p["postcode_partial"], f"only partial postcode '{s.postcode_to}' given")

    # City
    if s.city_match == "alias":
        pen("city_alias", p["city_alias"], f"city '{s.city_from}' is an alternate name of '{s.city_to}'")
    elif s.city_match == "fuzzy":
        sim = s.city_similarity or 0
        amount = min(round_half_up(p["city_fuzzy_base"] + (100 - sim) * p["city_fuzzy_per_point"]), p["city_fuzzy_max"])
        pen("city_fuzzy", amount, f"city '{s.city_from}' fuzzy-matched to '{s.city_to}' ({sim:.0f}%)")
    elif s.city_match == "ambiguous" and not s.ai_disambiguated:
        pen("city_ambiguous", p["city_ambiguous"], f"city '{s.city_from}' matches several places")
    elif s.city_match == "none":
        pen("city_not_found", p["city_not_found"], f"city '{s.city_from}' not found; located by postcode")

    if s.fields_fixed:
        pen("fields_fixed", p["fields_fixed"], "postcode/city values were split or swapped between columns")
    if s.ai_repaired:
        pen("ai_repaired", p["ai_repaired"], "input was repaired by AI")
    if s.ai_disambiguated:
        pen("ai_disambiguated", p["ai_disambiguated"], "AI chose between several candidate locations")

    # Consistency
    d = s.pc_city_distance_km
    if d is not None:
        if d > cfg["distance_km"]["far"]:
            pen("distance_far", p["distance_far"], f"postcode and city are {d:.0f} km apart")
        elif d >= cfg["distance_km"]["medium"]:
            pen("distance_medium", p["distance_medium"], f"postcode and city are {d:.0f} km apart")

    # Provider
    if s.provider_postcode_mismatch:
        pen("provider_postcode_mismatch", p["provider_postcode_mismatch"],
            "geocoder result lies outside the given postcode area")  # fmt: skip
    threshold = cfg.get("provider_low_confidence_below", {}).get(s.provider or "")
    if threshold is not None and s.provider_confidence is not None and s.provider_confidence < threshold:
        pen("provider_low_confidence", p["provider_low_confidence"],
            f"geocoder reported low confidence ({s.provider_confidence:g})")  # fmt: skip

    # Precision cap: the best score this precision allows; penalties are subtracted from it, so that
    # e.g. an ambiguous city-level match ranks below an unambiguous one.
    caps = cfg["caps"]
    cap, cap_text = 100, None
    if s.precision in ("city", "region", "country"):
        cap, cap_text = caps[s.precision], f"only {s.precision}-level location found (max {caps[s.precision]})"
    elif s.precision == "postcode" and s.street_given:
        cap = caps["street_given_postcode_only"]
        cap_text = f"street given but only postcode-level location found (max {cap})"
    value = max(0, min(100, cap - sum(r["penalty"] for r in reasons)))
    if cap < 100:
        reasons.append({"code": f"cap_{s.precision}", "penalty": 100 - cap, "text": cap_text})

    reasons.sort(key=lambda r: -r["penalty"])
    if value == 100:
        reasons = [{"code": "exact", "penalty": 0, "text": _positive_reason(s)}]
    return ScoreResult(value, band_for(value, cfg), reasons)


def _positive_reason(s: Signals) -> str:
    if s.precision in ("rooftop", "street"):
        return "exact address match"
    if s.postcode_found == "found" and s.city_match in ("exact", "alias", "from_postcode"):
        return "exact postcode and city match"
    if s.postcode_found == "found":
        return "exact postcode match"
    return "exact match"
