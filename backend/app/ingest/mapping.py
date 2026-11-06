"""Suggest which file columns hold street / house number / city / postcode / country."""

import re

from app.normalize.country import resolve_country
from app.normalize.text import fold

TARGET_FIELDS = ("street", "house_number", "city", "postcode", "country", "id", "full_address")

SYNONYMS: dict[str, list[str]] = {
    "street": ["street", "strasse", "straße", "str", "address", "address1", "address line 1", "addr", "adresse",
               "rue", "via", "calle", "straat", "ulica", "road", "street address", "anschrift", "strasse und hausnummer",
               "street name", "streetname", "line1", "address_1", "adres", "endereco", "gata", "vej"],
    "house_number": ["house number", "housenumber", "house no", "hausnummer", "hausnr", "hnr", "nr", "no",
                     "number", "numero", "número", "huisnummer", "building number", "street number", "civico"],
    "city": ["city", "town", "ort", "stadt", "ville", "citta", "città", "ciudad", "plaats", "stad", "miasto",
             "locality", "municipality", "gemeinde", "place", "wohnort", "cidade", "kommune", "by", "localite",
             "localité", "poblacion", "location city", "city name", "ortsname", "postort", "settlement"],
    "postcode": ["postcode", "post code", "postal code", "postalcode", "zip", "zipcode", "zip code", "plz",
                 "postleitzahl", "cp", "code postal", "cap", "codigo postal", "código postal", "kod pocztowy",
                 "postnummer", "postnr", "pc", "postal", "post_code", "zip_code", "npa", "psc", "irsz"],
    "country": ["country", "country code", "countrycode", "country_code", "land", "pays", "paese", "pais", "país",
                "kraj", "iso", "iso2", "iso code", "nation", "ctry", "cc", "landcode", "länderkennzeichen",
                "country name", "staat", "laenderkennzeichen", "land code", "country iso"],
    "id": ["id", "customer id", "customer_id", "kundennummer", "kunden nr", "ref", "reference", "key", "site id",
           "location id", "nummer", "record id", "external id"],
    "full_address": ["full address", "full_address", "complete address", "vollstandige adresse", "address full",
                     "formatted address", "adresse komplett", "gesamtadresse", "single line address"],
}  # fmt: skip

_FOLDED = {f: {fold(s) for s in syns} for f, syns in SYNONYMS.items()}
_PC_LIKE = re.compile(r"^(?:[A-Z]{1,2}-?)?\d{3,6}(?:[- ]\d{2,4})?$|^[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}$|^\d{4} ?[A-Z]{2}$", re.I)


def header_field(header: str) -> str | None:
    """Target field for a header name by exact (folded) synonym match."""
    h = fold(header)
    for f, syns in _FOLDED.items():
        if h in syns:
            return f
    return None


def _token_field(header: str) -> str | None:
    tokens = set(fold(header).split())
    for f in ("postcode", "country", "city", "house_number", "street"):
        if any(len(s) > 2 and s in tokens for s in _FOLDED[f]):
            return f
    return None


def _content_field(values: list[str]) -> str | None:
    vals = [v.strip() for v in values if v.strip()]
    if len(vals) < 3:
        return None
    n = len(vals)
    if sum(1 for v in vals if len(v) <= 40 and resolve_country(v).code) / n >= 0.8:
        return "country"
    if sum(1 for v in vals if _PC_LIKE.match(v)) / n >= 0.8:
        return "postcode"
    return None


def suggest_mapping(columns: list[str], rows: list[list[str]]) -> dict[str, str | None]:
    """{target_field: column name | None}. Header synonyms first, then header tokens, then content."""
    mapping: dict[str, str | None] = dict.fromkeys(TARGET_FIELDS)
    used: set[str] = set()
    for strategy in (header_field, _token_field):
        for col in columns:
            if col in used:
                continue
            f = strategy(col)
            if f and mapping[f] is None:
                mapping[f] = col
                used.add(col)
    sample = rows[:200]
    for i, col in enumerate(columns):
        if col in used:
            continue
        f = _content_field([r[i] for r in sample if i < len(r)])
        if f and mapping[f] is None:
            mapping[f] = col
            used.add(col)
    if mapping["full_address"] and mapping["street"] is None and mapping["city"] is None:
        pass  # a single address column; parsed into fields during ingestion
    elif mapping["full_address"] and (mapping["city"] or mapping["postcode"]):
        mapping["full_address"] = None
    return mapping


def mapping_problems(mapping: dict[str, str | None], columns: list[str], default_country: str | None) -> list[str]:
    problems = []
    unknown = [c for c in mapping.values() if c and c not in columns]
    if unknown:
        problems.append(f"unknown columns: {', '.join(unknown)}")
    has_full = bool(mapping.get("full_address"))
    if not (mapping.get("city") or mapping.get("postcode") or has_full):
        problems.append("map at least a city or a postcode column (or a full-address column)")
    if not mapping.get("country") and not default_country and not has_full:
        problems.append("map a country column or choose a default country for the job")
    chosen = [c for c in mapping.values() if c]
    if len(chosen) != len(set(chosen)):
        problems.append("a column is mapped to more than one field")
    return problems


def required_unmapped(mapping: dict[str, str | None]) -> bool:
    return not (mapping.get("city") or mapping.get("postcode") or mapping.get("full_address"))


def split_full_address(value: str) -> dict[str, str]:
    """Best-effort split of 'Marienplatz 1, 80331 München, Germany' into fields."""
    from app.normalize.fields import fix_fields

    parts = [p.strip() for p in value.split(",") if p.strip()]
    out = {"street": "", "house_number": "", "city": "", "postcode": "", "country": ""}
    if len(parts) > 1 and resolve_country(parts[-1]).code and not any(ch.isdigit() for ch in parts[-1]):
        out["country"] = parts.pop()
    for i, part in enumerate(parts):
        fix = fix_fields(part, "")
        if fix.postcode:
            out["postcode"], out["city"] = fix.postcode, fix.city
            out["street"] = ", ".join(parts[:i])
            return out
    if len(parts) >= 2:
        out["street"], out["city"] = ", ".join(parts[:-1]), parts[-1]
    elif parts:
        out["city"] = parts[0]
    return out
