import re
import unicodedata

from anyascii import anyascii

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_SPACES = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]|_")
_GERMAN_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})
_PARENS = re.compile(r"\s*\([^)]*\)\s*")
# Trailing qualifiers that distinguish same-named places ("Frankfurt am Main", "Stratford-upon-Avon").
_QUALIFIER = re.compile(
    r"^(?P<base>.+?)\s+(?:am|an der|an den|im|in der|in|bei|ob der|auf der|vor der|unter|upon|on|sur|sous|en|"
    r"lès|les|aan de|aan den|ad|nad|pod)\s+.+$",
    re.IGNORECASE,
)


def clean(value: object) -> str:
    """T0 cleanup: NFC, drop control characters, collapse whitespace, trim."""
    if value is None:
        return ""
    s = unicodedata.normalize("NFC", str(value))
    s = _CONTROL.sub(" ", s)
    return _SPACES.sub(" ", s).strip()


def fold(value: str) -> str:
    """Accent-, case- and punctuation-insensitive key: 'Frankfurt (Oder)' -> 'frankfurt oder'."""
    s = anyascii(value).lower()
    s = _PUNCT.sub(" ", s)
    return _SPACES.sub(" ", s).strip()


def fold_variants(value: str) -> set[str]:
    """Folded forms including the German umlaut transliteration (München -> munchen, muenchen)."""
    out = {fold(value)}
    translit = value.translate(_GERMAN_TRANSLIT)
    if translit != value:
        out.add(fold(translit))
    out.discard("")
    return out


def base_name(name: str) -> str | None:
    """Name without disambiguating qualifier, or None if there is none.
    'Frankfurt (Oder)' -> 'Frankfurt', 'Frankfurt am Main' -> 'Frankfurt'."""
    stripped = _PARENS.sub(" ", name).strip()
    stripped = re.sub(r"[-‐]", " ", stripped)
    m = _QUALIFIER.match(stripped)
    if m:
        stripped = m.group("base")
    stripped = stripped.strip(" ,/")
    return stripped if stripped and fold(stripped) != fold(name) else None
