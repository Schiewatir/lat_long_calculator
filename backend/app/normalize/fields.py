"""Detect values in the wrong field: '80331 München' in the city column, swapped city/postcode."""

import re
from dataclasses import dataclass

_PC_TOKEN = r"(?:[A-Z]{1,2}-)?\d{3,6}(?:[- ]\d{2,4})?|[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}|\d{4} ?[A-Z]{2}"
_LEADING_PC = re.compile(rf"^(?P<pc>{_PC_TOKEN})[\s,]+(?P<city>\D.*)$", re.IGNORECASE)
_TRAILING_PC = re.compile(rf"^(?P<city>.*\D)[\s,]+(?P<pc>{_PC_TOKEN})$", re.IGNORECASE)
_HAS_DIGIT = re.compile(r"\d")


@dataclass
class FieldFix:
    city: str
    postcode: str
    reason: str | None


def fix_fields(city: str, postcode: str) -> FieldFix:
    """Split combined postcode+city values and undo swapped city/postcode columns."""
    if city and not postcode:
        for pattern in (_LEADING_PC, _TRAILING_PC):
            if m := pattern.match(city):
                return FieldFix(m.group("city").strip(" ,"), m.group("pc").strip(), "postcode split out of city field")
    if city and postcode and not _HAS_DIGIT.search(postcode) and re.fullmatch(_PC_TOKEN, city, re.IGNORECASE):
        return FieldFix(postcode, city, "city and postcode fields were swapped")
    if city and postcode and city.strip() == postcode.strip():
        return FieldFix(city, postcode, None)
    if postcode and not city:
        for pattern in (_LEADING_PC, _TRAILING_PC):
            if m := pattern.match(postcode):
                return FieldFix(m.group("city").strip(" ,"), m.group("pc").strip(), "city split out of postcode field")
    return FieldFix(city, postcode, None)
