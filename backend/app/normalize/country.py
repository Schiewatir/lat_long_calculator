"""Resolve free-text country input to ISO 3166-1 alpha-2."""

import gettext
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pycountry
import yaml
from rapidfuzz import fuzz, process

from app.normalize.text import fold

TRANSLATION_LANGS = [
    "de", "fr", "es", "it", "nl", "pt", "pl", "sv", "da", "nb", "nn", "fi", "cs", "sk", "hu", "ro",
    "hr", "sl", "et", "lv", "lt", "tr", "ca", "el", "ru", "uk", "bg", "is", "ga",
]  # fmt: skip
FUZZY_THRESHOLD = 85
MIN_FUZZY_LENGTH = 4


@dataclass(frozen=True)
class CountryResolution:
    code: str | None
    method: str | None  # exact | alias | fuzzy | job_default | inferred | None
    score: float | None = None


def _compact(s: str) -> str:
    return s.replace(" ", "")


@lru_cache
def alias_table() -> tuple[dict[str, str], dict[str, str]]:
    """(aliases, fuzzy_names): folded alias -> alpha2. Priority: ISO codes > curated > names > translations.
    Aliases that map to more than one country across sources are dropped (except ISO codes)."""
    aliases: dict[str, str] = {}
    conflicts: set[str] = set()
    names: dict[str, str] = {}

    def add(alias: str, code: str, *, name: bool = False) -> None:
        for key in {fold(alias), _compact(fold(alias))}:
            if not key:
                continue
            if key in aliases and aliases[key] != code:
                conflicts.add(key)
                continue
            aliases.setdefault(key, code)
            if name and len(key) >= MIN_FUZZY_LENGTH:
                names.setdefault(key, code)

    for c in pycountry.countries:
        add(c.alpha_3, c.alpha_2)
        add(c.numeric, c.alpha_2)
        add(str(int(c.numeric)), c.alpha_2)
    curated = yaml.safe_load((Path(__file__).parent / "data" / "country_variants.yaml").read_text("utf-8"))
    for code, variants in curated.items():
        for v in variants:
            add(str(v), code, name=len(str(v)) > 3)
    for c in pycountry.countries:
        for attr in ("name", "official_name", "common_name"):
            if value := getattr(c, attr, None):
                add(value, c.alpha_2, name=True)
    for lang in TRANSLATION_LANGS:
        try:
            t = gettext.translation("iso3166-1", pycountry.LOCALES_DIR, languages=[lang])
        except OSError:
            continue
        for c in pycountry.countries:
            add(t.gettext(c.name), c.alpha_2, name=True)

    # ISO alpha-2 codes are authoritative; never let a conflicting alias shadow them.
    for key in conflicts:
        if len(key) != 2:
            aliases.pop(key, None)
            names.pop(key, None)
    return aliases, names


ISO2 = {c.alpha_2 for c in pycountry.countries} | {"XK"}


def resolve_country(raw: str) -> CountryResolution:
    value = raw.strip()
    if not value:
        return CountryResolution(None, None)
    if len(value) == 2 and value.upper() in ISO2:
        return CountryResolution(value.upper(), "exact")
    aliases, names = alias_table()
    key = fold(value)
    for k in (key, _compact(key)):
        if k in aliases:
            return CountryResolution(aliases[k], "alias")
    if len(key) >= MIN_FUZZY_LENGTH:
        match = process.extractOne(key, list(names.keys()), scorer=fuzz.ratio, score_cutoff=FUZZY_THRESHOLD)
        if match:
            return CountryResolution(names[match[0]], "fuzzy", round(match[1], 1))
    return CountryResolution(None, None)


def country_name(code: str | None) -> str:
    if not code:
        return ""
    c = pycountry.countries.get(alpha_2=code)
    return c.name if c else code
