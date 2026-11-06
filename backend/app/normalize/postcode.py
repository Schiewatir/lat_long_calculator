"""Per-country postcode validation and repair of spreadsheet/typing corruptions."""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

MAX_PAD = 2
_EXCEL_FLOAT = re.compile(r"^(\d+)\.0+$")
_PREFIX = re.compile(r"^([A-Z]{1,3})\s*[-–]?\s*(\d[\d\s\-]*)$")
_SEPARATORS = re.compile(r"[\s\-–.]")
_GB_FULL = re.compile(r"^([A-Z]{1,2}\d[A-Z\d]?)(\d[A-Z]{2})$")
_GB_OUTWARD = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?$")
_GENERIC = re.compile(r"^[A-Z0-9]{2,10}$")


@dataclass
class PostcodeResult:
    value: str | None  # canonical form, or None if missing/invalid
    status: str  # missing | valid | repaired | invalid_format
    repairs: list[tuple[str, str, str]] = field(default_factory=list)  # (from, to, reason)
    lookup_keys: list[str] = field(default_factory=list)  # compact keys, most specific first
    partial: bool = False  # only a prefix (e.g. GB outward code) is available


@lru_cache
def rules() -> dict[str, dict]:
    return yaml.safe_load((Path(__file__).parent / "data" / "postcode_rules.yaml").read_text("utf-8"))


def compact(value: str) -> str:
    return _SEPARATORS.sub("", value.upper())


def _template_compact(template: str) -> str:
    return compact(template)


def _placeholders(template: str) -> int:
    return sum(1 for c in template if c in "#@*")


def _literal_prefix(template: str) -> str:
    out = []
    for c in _template_compact(template):
        if c in "#@*":
            break
        out.append(c)
    return "".join(out)


def _matches(code: str, template: str) -> bool:
    t = _template_compact(template)
    if len(code) != len(t):
        return False
    for ch, sym in zip(code, t, strict=True):
        if sym == "#":
            if not ch.isdigit():
                return False
        elif sym == "@":
            if not ("A" <= ch <= "Z"):
                return False
        elif sym == "*":
            if not ch.isalnum():
                return False
        elif ch != sym:
            return False
    return True


def _render(code: str, template: str) -> str:
    out, i = [], 0
    t_compact = _template_compact(template)
    for sym in template:
        if i < len(code) and sym in "#@*":
            out.append(code[i])
            i += 1
        elif sym.upper() in t_compact and i < len(code) and sym.isalnum():
            out.append(code[i])  # literal letter/digit inside the template, e.g. "LT-"
            i += 1
        else:
            out.append(sym)
    return "".join(out)


def _strip_country_prefix(value: str, country: str) -> str | None:
    from app.normalize.country import resolve_country

    m = _PREFIX.match(value)
    if not m:
        return None
    prefix = m.group(1)
    if prefix == country or resolve_country(prefix).code == country:
        return m.group(2)
    return None


def _format(code: str, country: str) -> tuple[str | None, bool, str | None]:
    """(canonical, partial, pad_reason) for a compact code, or (None, False, None) if invalid."""
    rule = rules().get(country)
    if rule is None:
        return (code, False, None) if _GENERIC.match(code) else (None, False, None)
    if rule.get("special") == "gb":
        if m := _GB_FULL.match(code):
            return f"{m.group(1)} {m.group(2)}", False, None
        if _GB_OUTWARD.match(code):
            return code, True, None
        return None, False, None
    formats: list[str] = rule["formats"]
    for tpl in formats:
        if _matches(code, tpl):
            partial = _placeholders(tpl) < _placeholders(formats[0])
            return _render(code, tpl), partial, None
    if rule.get("pad") and code.isdigit():
        for tpl in formats:
            want = len(_template_compact(tpl))
            if 0 < want - len(code) <= MAX_PAD and set(_template_compact(tpl)) == {"#"}:
                padded = code.rjust(want, "0")
                return _render(padded, tpl), False, f"zero-padded {code}→{padded}"
    # Codes whose canonical form carries a literal prefix (LV-1050) given without it.
    for tpl in formats:
        literal = _literal_prefix(tpl)
        if literal and _matches(literal + code, tpl):
            return _render(literal + code, tpl), False, None
    return None, False, None


def lookup_keys(canonical: str, country: str) -> tuple[list[str], bool]:
    """Compact gazetteer keys to try, most specific first, and whether only a prefix was usable."""
    code = compact(canonical)
    keys = [code]
    rule = rules().get(country) or {}
    if rule.get("special") == "gb":
        if m := _GB_FULL.match(code):
            keys.append(m.group(1))
    elif (n := rule.get("lookup_prefix")) and len(code) > n:
        keys.append(code[:n])
    for tpl in rule.get("formats", []):
        literal = _literal_prefix(tpl)
        if literal and code.startswith(literal):
            keys.append(code[len(literal):])  # gazetteer may store the code without "LV-"
    if not code.startswith(country):
        keys.append(country + code)
    return list(dict.fromkeys(keys)), False


def repair_postcode(raw: str, country: str | None) -> PostcodeResult:
    value = raw.strip()
    if not value:
        return PostcodeResult(None, "missing")
    repairs: list[tuple[str, str, str]] = []
    substantive = False

    if m := _EXCEL_FLOAT.match(value):
        repairs.append((value, m.group(1), "spreadsheet number artifact removed"))
        value = m.group(1)
        substantive = True
    value = value.upper()

    if country is None:
        code = compact(value)
        if not _GENERIC.match(code):
            return PostcodeResult(None, "invalid_format", repairs)
        return PostcodeResult(code, "repaired" if substantive else "valid", repairs, [code])

    formats = (rules().get(country) or {}).get("formats", [])
    prefixed_format = bool(formats and _literal_prefix(formats[0]))
    if not prefixed_format and (stripped := _strip_country_prefix(value, country)) is not None:
        repairs.append((value, stripped, "country prefix removed"))
        value = stripped
        substantive = True

    code = compact(value)
    rule = rules().get(country) or {}
    if rule.get("zip4") and len(code) == 9 and code.isdigit():
        repairs.append((value, code[:5], "ZIP+4 reduced to 5-digit ZIP"))
        code = code[:5]
        substantive = True

    canonical, partial, pad_reason = _format(code, country)
    if canonical is None:
        return PostcodeResult(None, "invalid_format", repairs)
    if pad_reason:
        repairs.append((code, canonical, pad_reason))
        substantive = True
    last = repairs[-1][1] if repairs else raw.strip()
    if canonical != last:
        repairs.append((last, canonical, "canonical formatting"))
    keys, _ = lookup_keys(canonical, country)
    status = "repaired" if substantive else "valid"
    return PostcodeResult(canonical, status, repairs, keys, partial)


def format_matches(raw: str, country: str) -> bool:
    """Whether the postcode is plausibly from this country (after the same repairs)."""
    return repair_postcode(raw, country).status in ("valid", "repaired")
