"""Deterministic address normalization (tiers T0 + T1).

Country, postcode and city are cross-checked against each other and the gazetteer. Every change
to an input value is recorded as a `Correction`. AI repair (T2) feeds its output back through
`normalize()` so that nothing the model suggests is trusted without this validation."""

from dataclasses import asdict, dataclass, field
from typing import Any

from app.gazetteer.base import Gazetteer, Place, PostalRec, centroid, haversine_km
from app.normalize.country import CountryResolution, country_name, resolve_country
from app.normalize.fields import fix_fields
from app.normalize.postcode import compact, repair_postcode
from app.normalize.text import clean, fold, fold_variants

CITY_FUZZY_CUTOFF = 85.0
LINK_KM = 15.0  # a city within this distance of the postcode centroid is consistent with it
CONFLICT_KM = 50.0
DOMINANT_POPULATION_RATIO = 50
ABBREVIATIONS: dict[str, dict[str, list[str]]] = {
    "st": {"DE": ["sankt"], "AT": ["sankt"], "CH": ["sankt", "saint"], "FR": ["saint"], "BE": ["saint", "sint"],
           "CA": ["saint"], "*": ["saint"]},
    "ste": {"*": ["sainte"]},
    "mt": {"*": ["mount"]},
    "ft": {"*": ["fort"]},
}  # fmt: skip


@dataclass
class RawAddress:
    street: str = ""
    house_number: str = ""
    city: str = ""
    postcode: str = ""
    country: str = ""

    def cleaned(self) -> "RawAddress":
        return RawAddress(*(clean(v) for v in (self.street, self.house_number, self.city, self.postcode, self.country)))


@dataclass
class Correction:
    field: str
    from_: str
    to: str
    tier: str  # deterministic | ai | job_default
    reason: str

    @property
    def sentence(self) -> str:
        if self.from_:
            return f"{self.field} '{self.from_}' → '{self.to}': {self.reason}"
        return f"{self.field} set to '{self.to}': {self.reason}"

    def to_dict(self) -> dict:
        return {"field": self.field, "from": self.from_, "to": self.to, "tier": self.tier,
                "reason": self.reason, "sentence": self.sentence}  # fmt: skip


@dataclass
class NormResult:
    street: str = ""
    house_number: str = ""
    city: str | None = None
    postcode: str | None = None
    country: str | None = None
    country_method: str | None = None
    country_score: float | None = None
    country_overridden_from: str | None = None
    postcode_status: str = "missing"  # missing | valid | repaired | invalid_format
    postcode_found: str = "n/a"  # found | not_found | unverifiable | n/a
    postcode_partial: bool = False
    pc_lat: float | None = None
    pc_lon: float | None = None
    pc_place: str | None = None  # the postcode's own place name in the gazetteer
    city_match: str = "missing"  # exact | alias | fuzzy | ambiguous | none | missing | from_postcode
    city_similarity: float | None = None
    city_lat: float | None = None
    city_lon: float | None = None
    city_candidates: list[dict] = field(default_factory=list)
    pc_city_linked: bool | None = None
    pc_city_distance_km: float | None = None
    fields_fixed: bool = False
    resolution: str = "unresolved"
    flags: list[str] = field(default_factory=list)
    corrections: list[Correction] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["corrections"] = [c.to_dict() for c in self.corrections]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "NormResult":
        d = dict(d)
        d["corrections"] = [
            Correction(c["field"], c["from"], c["to"], c["tier"], c["reason"]) for c in d.get("corrections", [])
        ]
        return cls(**d)


# ----------------------------------------------------------------------------- helpers


def city_query_variants(city: str, country: str | None) -> list[str]:
    variants = list(fold_variants(city))
    for v in list(variants):
        tokens = v.split()
        for i, tok in enumerate(tokens):
            if tok in ABBREVIATIONS:
                expansions = ABBREVIATIONS[tok].get(country or "*") or ABBREVIATIONS[tok].get("*", [])
                for exp in expansions:
                    variants.append(" ".join([*tokens[:i], exp, *tokens[i + 1 :]]))
    return list(dict.fromkeys(variants))


def _postal_names_match(recs: list[PostalRec], city_variants: list[str]) -> bool:
    from rapidfuzz import fuzz

    for p in recs:
        pnames = fold_variants(p.place_name)
        for cv in city_variants:
            for pn in pnames:
                if cv == pn or set(cv.split()) <= set(pn.split()) or fuzz.ratio(cv, pn) >= CITY_FUZZY_CUTOFF:
                    return True
    return False


def _place_linked(place: Place, recs: list[PostalRec], pc_center: tuple[float, float] | None) -> bool:
    for p in recs:
        if fold_variants(p.place_name) & place.names:
            return True
    return pc_center is not None and haversine_km(place.lat, place.lon, *pc_center) <= LINK_KM


def _pc_candidate_keys(raw_pc: str) -> list[str]:
    """Country-agnostic compact keys for cross-country postcode lookups (incl. lost leading zeros)."""
    code = compact(raw_pc.strip().removesuffix(".0"))
    keys = [code]
    if code.isdigit():
        keys += ["0" + code, "00" + code]
    return [k for k in keys if k]


@dataclass
class _CityMatch:
    kind: str
    name: str | None = None
    similarity: float | None = None
    lat: float | None = None
    lon: float | None = None
    candidates: list[Place] = field(default_factory=list)
    linked: bool | None = None


async def match_city(
    gaz: Gazetteer, city: str, country: str, postal: list[PostalRec], pc_center: tuple[float, float] | None
) -> _CityMatch:
    if not city:
        return _CityMatch("missing")
    index = await gaz.city_index(country)
    variants = city_query_variants(city, country)
    hits = index.exact(variants)
    kind, similarity = "exact", None
    if not hits:
        best: list[tuple[str, float]] = []
        for v in variants:
            best += index.fuzzy(v, CITY_FUZZY_CUTOFF)
        if not best:
            return _CityMatch("none")
        top = max(s for _, s in best)
        names = {n for n, s in best if s >= top - 2}
        hits = index.exact(names)
        kind, similarity = "fuzzy", round(top, 1)

    places = index.cluster(hits)
    if postal:
        linked = [p for p in places if _place_linked(p, postal, pc_center)]
        if linked:
            places = linked
        elif len(places) > 1:
            places.sort(key=lambda p: -p.population)
    chosen: Place | None = None
    if len(places) == 1:
        chosen = places[0]
    else:
        places.sort(key=lambda p: -p.population)
        top, second = places[0], places[1]
        if top.population > 0 and top.population >= DOMINANT_POPULATION_RATIO * max(second.population, 1):
            chosen = top
    if chosen is None:
        return _CityMatch("ambiguous", similarity=similarity, candidates=places[:5])
    if kind == "exact" and not (set(variants) & fold_variants(chosen.name)):
        kind = "alias"
    is_linked = _place_linked(chosen, postal, pc_center) if postal else None
    return _CityMatch(kind, chosen.name, similarity, chosen.lat, chosen.lon, [chosen], is_linked)


# ----------------------------------------------------------------------------- country inference


@dataclass
class _Evidence:
    pc_found: bool
    city_found: bool
    linked: bool


async def _alternative_countries(
    gaz: Gazetteer, raw_pc: str, city: str
) -> dict[str, _Evidence]:
    pc_hits: dict[str, list[PostalRec]] = {}
    if raw_pc:
        for key in _pc_candidate_keys(raw_pc):
            for country, recs in (await gaz.postal_any(key)).items():
                if country in pc_hits:
                    continue
                repaired = repair_postcode(raw_pc, country)
                if repaired.value and key in repaired.lookup_keys:
                    pc_hits[country] = recs
    name_hits: dict[str, list] = {}
    if city:
        for v in city_query_variants(city, None):
            for rec in await gaz.names_any(v):
                name_hits.setdefault(rec.country, []).append(rec)
    variants = city_query_variants(city, None) if city else []
    out: dict[str, _Evidence] = {}
    for country in set(pc_hits) | set(name_hits):
        recs = pc_hits.get(country, [])
        center = centroid((p.lat, p.lon) for p in recs)
        linked = False
        if recs and city:
            linked = _postal_names_match(recs, variants) or any(
                center is not None and haversine_km(n.lat, n.lon, *center) <= LINK_KM for n in name_hits.get(country, [])
            )
        out[country] = _Evidence(bool(recs), country in name_hits, linked)
    return out


async def _country_supported(gaz: Gazetteer, country: str, raw_pc: str, city: str) -> tuple[bool, bool]:
    """(postcode supports country, city supports country)."""
    pc_ok = False
    if raw_pc:
        repaired = repair_postcode(raw_pc, country)
        if repaired.value:
            if await gaz.has_postal_data(country):
                pc_ok = bool((await gaz.postal(country, repaired.lookup_keys))[1])
            else:
                pc_ok = True  # format fits and nothing contradicts it
    city_ok = False
    if city:
        m = await match_city(gaz, city, country, [], None)
        city_ok = m.kind in ("exact", "alias", "fuzzy", "ambiguous")
    return pc_ok, city_ok


async def resolve_country_with_evidence(
    gaz: Gazetteer, raw_country: str, raw_pc: str, city: str, default_country: str | None
) -> tuple[CountryResolution, str | None, str | None]:
    """(resolution, overridden_from, reason)."""
    given = resolve_country(raw_country) if raw_country else CountryResolution(None, None)
    if given.code is None and not raw_country and default_country:
        given = CountryResolution(default_country, "job_default")

    if given.code is not None:
        pc_ok, city_ok = await _country_supported(gaz, given.code, raw_pc, city)
        contradicted = (not raw_pc or not pc_ok) and (not city or not city_ok) and (raw_pc or city)
        if not contradicted:
            return given, None, None
        if raw_pc and city:
            alts = await _alternative_countries(gaz, raw_pc, city)
            strong = [c for c, e in alts.items() if c != given.code and e.pc_found and e.linked]
            if len(strong) == 1:
                new = strong[0]
                reason = (f"postcode {raw_pc} and city {city} match {new} ({country_name(new)}), "
                          f"not {given.code}")  # fmt: skip
                return CountryResolution(new, "inferred"), given.code, reason
        return given, None, None

    # No usable country: infer only from unambiguous evidence.
    alts = await _alternative_countries(gaz, raw_pc, city)
    if raw_pc and city:
        strong = [c for c, e in alts.items() if e.pc_found and e.linked]
    elif raw_pc:
        strong = [c for c, e in alts.items() if e.pc_found]
    else:
        strong = [c for c, e in alts.items() if e.city_found]
    if len(strong) == 1:
        new = strong[0]
        what = " and ".join(x for x in (f"postcode {raw_pc}" if raw_pc else "", f"city {city}" if city else "") if x)
        return CountryResolution(new, "inferred"), raw_country or None, f"{what} only match {new} ({country_name(new)})"
    return CountryResolution(None, None), None, None


# ----------------------------------------------------------------------------- main entry


async def normalize(raw: RawAddress, gaz: Gazetteer, default_country: str | None = None, tier: str = "deterministic") -> NormResult:
    a = raw.cleaned()
    r = NormResult(street=a.street, house_number=a.house_number)

    fix = fix_fields(a.city, a.postcode)
    if fix.reason:
        r.fields_fixed = True
        if fix.city != a.city:
            r.corrections.append(Correction("city", a.city, fix.city, tier, fix.reason))
        if fix.postcode != a.postcode:
            r.corrections.append(Correction("postcode", a.postcode, fix.postcode, tier, fix.reason))
    city_in, pc_in = fix.city, fix.postcode

    res, overridden_from, reason = await resolve_country_with_evidence(gaz, a.country, pc_in, city_in, default_country)
    r.country, r.country_method, r.country_score = res.code, res.method, res.score
    if res.code is None:
        r.flags.append("country_unknown")
        r.city = city_in or None
        r.postcode = pc_in or None
        r.postcode_status = "missing" if not pc_in else "invalid_format"
        r.resolution = "unresolved"
        return r
    if res.method == "inferred":
        r.country_overridden_from = overridden_from
        r.corrections.append(Correction("country", a.country, res.code, tier, reason or "inferred"))
    elif res.method in ("alias", "fuzzy"):
        why = "alias" if res.method == "alias" else f"fuzzy match ({res.score:.0f}%)"
        r.corrections.append(Correction("country", a.country, res.code, tier, why))
    elif res.method == "job_default":
        r.corrections.append(Correction("country", "", res.code, "job_default", "country from job default"))
    country = res.code

    # Postcode
    pc = repair_postcode(pc_in, country)
    r.postcode_status, r.postcode_partial = pc.status, pc.partial
    r.postcode = pc.value if pc.value else (pc_in or None)
    for frm, to, why in pc.repairs:
        r.corrections.append(Correction("postcode", frm, to, tier, why))
    postal: list[PostalRec] = []
    if pc.value:
        _, postal = await gaz.postal(country, pc.lookup_keys)
        if postal:
            r.postcode_found = "found"
        elif await gaz.has_postal_data(country):
            r.postcode_found = "not_found"
            r.flags.append("postcode_not_found")
        else:
            r.postcode_found = "unverifiable"
            r.flags.append("postcode_unverifiable")
    elif pc.status == "invalid_format":
        r.flags.append("postcode_invalid_format")
    pc_center = centroid((p.lat, p.lon) for p in postal)

    # City
    m = await match_city(gaz, city_in, country, postal, pc_center)
    # The postcode may contain more places than the city index knows about; a same-named postal
    # place counts as consistent before a conflict is declared.
    if (m.kind in ("exact", "alias", "fuzzy") and postal and not m.linked
            and _postal_names_match(postal, city_query_variants(city_in, country))):  # fmt: skip
        m.linked = True
    r.city_match, r.city_similarity = m.kind, m.similarity
    r.city_candidates = [
        {"name": p.name, "lat": p.lat, "lon": p.lon, "population": p.population} for p in m.candidates
    ]
    if m.kind in ("exact", "alias", "fuzzy"):
        r.city, r.city_lat, r.city_lon = m.name, m.lat, m.lon
        if m.name and fold(m.name) != fold(city_in):
            if m.kind == "fuzzy":
                why = f"fuzzy match ({m.similarity or 0:.0f}%)"
            else:
                why = "known alternate name" if m.kind == "alias" else "spelling normalized"
            r.corrections.append(Correction("city", city_in, m.name, tier, why))
    elif m.kind == "missing" and postal:
        name = _most_common(p.place_name for p in postal)
        r.city, r.city_match = name, "from_postcode"
        r.corrections.append(Correction("city", "", name, tier, f"filled from postcode {r.postcode}"))
    else:
        r.city = city_in or None
        if m.kind == "none":
            r.flags.append("city_not_found")
        if m.kind == "ambiguous":
            r.flags.append("city_ambiguous")

    if pc_center:
        r.pc_lat, r.pc_lon = pc_center
        r.pc_place = _most_common(p.place_name for p in postal)
    if postal and m.kind in ("exact", "alias", "fuzzy"):
        r.pc_city_linked = bool(m.linked)
        if not m.linked and pc_center and m.lat is not None and m.lon is not None:
            r.pc_city_distance_km = round(haversine_km(m.lat, m.lon, *pc_center), 1)

    r.resolution = _resolution(r)
    return r


def _most_common(values) -> str:
    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return max(counts, key=lambda k: counts[k])


def _resolution(r: NormResult) -> str:
    if r.country is None:
        return "unresolved"
    pc_ok = r.postcode_found == "found"
    city_ok = r.city_match in ("exact", "alias", "fuzzy", "from_postcode")
    if r.city_match == "ambiguous" and not pc_ok:
        return "ambiguous"
    if r.pc_city_distance_km is not None and r.pc_city_distance_km > CONFLICT_KM:
        r.flags.append("postcode_city_conflict")
        return "unresolved"
    if pc_ok or city_ok:
        return "resolved"
    return "unresolved"
