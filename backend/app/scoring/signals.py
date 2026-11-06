from app.geocode.base import GeoCandidate
from app.normalize.pipeline import NormResult, RawAddress
from app.scoring.engine import ScoreResult, Signals, score


def signals_for(
    raw: RawAddress, n: NormResult, cand: GeoCandidate | None, flags: list[str] | tuple[str, ...] = ()
) -> Signals:
    a = raw.cleaned()
    provider = cand.source.split(":", 1)[1] if cand and cand.source.startswith("provider:") else None
    city_match = n.city_match
    if cand and "ambiguous" not in cand.flags and city_match == "ambiguous" and n.postcode_found == "found":
        city_match = "from_postcode"  # postcode settled it
    return Signals(
        country_method=n.country_method,
        country_from=n.country_overridden_from if n.country_method == "inferred" else a.country,
        country_to=n.country,
        country_score=n.country_score,
        postcode_status=n.postcode_status,
        postcode_found=n.postcode_found,
        postcode_partial=n.postcode_partial,
        postcode_from=a.postcode,
        postcode_to=n.postcode,
        city_match=city_match,
        city_similarity=n.city_similarity,
        city_from=a.city,
        city_to=n.city,
        fields_fixed=n.fields_fixed,
        ai_repaired="ai_repaired" in flags,
        ai_disambiguated="ai_disambiguated" in flags,
        pc_city_distance_km=n.pc_city_distance_km,
        precision=cand.precision if cand else "none",
        street_given=bool(n.street),
        provider=provider,
        provider_confidence=cand.provider_confidence if cand else None,
        provider_postcode_mismatch=bool(cand and "postcode_mismatch" in cand.flags),
    )


def score_candidate(
    raw: RawAddress, n: NormResult, cand: GeoCandidate | None, flags: list[str] | tuple[str, ...], config: dict
) -> ScoreResult:
    return score(signals_for(raw, n, cand, flags), config)
