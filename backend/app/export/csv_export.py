"""CSV export: UTF-8 with BOM, RFC 4180 quoting, CRLF, original columns first, formula-injection safe."""

import csv
import io
import re
from collections.abc import Iterable

ADDED_COLUMNS = ["lat", "lon", "confidence", "confidence_band", "precision_level", "geocode_source", "matched_street",
                 "matched_city", "matched_postcode", "matched_country", "corrections", "status", "reviewed_by",
                 "reviewed_at"]  # fmt: skip
BOM = "﻿"
_NUMBER = re.compile(r"^[+-]?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?$")
_SAFE_FILENAME = re.compile(r"[^\w\-]+")


def header(original: list[str]) -> list[str]:
    taken = set(original)
    return [*original, *(f"geo_{c}" if c in taken else c for c in ADDED_COLUMNS)]


def safe_cell(value: object) -> str:
    """Neutralize spreadsheet formulas: =, +, -, @ at the start of a non-numeric value."""
    s = "" if value is None else str(value)
    if s and s[0] in "=+-@" and not _NUMBER.match(s):
        return "'" + s
    if s and s[0] in "\t\r":
        return "'" + s
    return s


def coord(v: float | None) -> str:
    return "" if v is None else f"{v:.6f}"


def export_row(raw: list[str], width: int, added: dict) -> list[str]:
    original = [safe_cell(v) for v in (list(raw) + [""] * width)[:width]]
    return original + [safe_cell(added.get(c, "")) if c not in ("lat", "lon") else coord(added.get(c))
                       for c in ADDED_COLUMNS]  # fmt: skip


def csv_lines(rows: Iterable[list[str]]) -> Iterable[str]:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    for r in rows:
        w.writerow(r)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)


def filename(job_name: str, scope: str, when) -> str:
    base = _SAFE_FILENAME.sub("_", job_name).strip("_")[:80] or "export"
    return f"{base}_{scope}_{when.strftime('%Y%m%d-%H%M')}.csv"
