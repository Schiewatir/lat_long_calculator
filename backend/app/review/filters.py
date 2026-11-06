"""The one filter model shared by the rows list, map, bulk actions and export."""

import base64
import json
import uuid
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, Text, and_, cast, func, literal, or_, tuple_
from sqlalchemy.dialects.postgresql import ARRAY, array

from app.models import Row

Band = Literal["High", "Medium", "Low", "Failed"]
Status = Literal["pending", "confirmed", "edited", "rejected"]
CorrectionType = Literal["country_changed", "postcode_repaired", "city_fuzzy", "ai_repaired", "ai_disambiguated",
                         "fields_fixed", "manual", "candidate_picked"]  # fmt: skip
SortField = Literal["row_index", "score", "status", "m_city", "m_country", "in_city", "precision"]


class RowFilter(BaseModel):
    bands: list[Band] | None = None
    score_min: int | None = Field(default=None, ge=0, le=100)
    score_max: int | None = Field(default=None, ge=0, le=100)
    statuses: list[Status] | None = None
    precisions: list[str] | None = None
    corrections: list[CorrectionType] | None = None
    errors_only: bool = False
    countries: list[str] | None = None
    q: str | None = Field(default=None, max_length=200)
    row_ids: list[int] | None = Field(default=None, max_length=10_000)

    def conditions(self, job_id: uuid.UUID) -> list[ColumnElement[bool]]:
        c: list[ColumnElement[bool]] = [Row.job_id == job_id]
        if self.bands:
            c.append(Row.band.in_(self.bands))
        if self.score_min is not None:
            c.append(Row.score >= self.score_min)
        if self.score_max is not None:
            c.append(Row.score <= self.score_max)
        if self.statuses:
            c.append(Row.status.in_(self.statuses))
        if self.precisions:
            c.append(Row.precision.in_(self.precisions))
        if self.corrections:
            c.append(Row.flags.overlap(cast(array(self.corrections), ARRAY(Text))))
        if self.errors_only:
            c.append(Row.error.is_not(None))
        if self.countries:
            c.append(Row.m_country.in_([x.upper() for x in self.countries]))
        if self.q and self.q.strip():
            c.append(Row.search.contains(self.q.strip().lower(), autoescape=True))
        if self.row_ids is not None:
            c.append(Row.id.in_(self.row_ids))
        return c

    def where(self, job_id: uuid.UUID) -> ColumnElement[bool]:
        return and_(*self.conditions(job_id))

    def is_empty(self) -> bool:
        return self.model_dump(exclude_defaults=True) == {}


def sort_expr(field: SortField):
    col = getattr(Row, field)
    if field in ("score",):
        return func.coalesce(col, -1)
    if field == "row_index":
        return col
    return func.coalesce(col, "")


def encode_cursor(value, row_index: int) -> str:
    return base64.urlsafe_b64encode(json.dumps([value, row_index]).encode()).decode()


def decode_cursor(cursor: str) -> tuple:
    value, idx = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    return value, idx


def keyset_condition(field: SortField, desc: bool, cursor: str) -> ColumnElement[bool]:
    value, idx = decode_cursor(cursor)
    expr = sort_expr(field)
    if field == "row_index":
        return Row.row_index < idx if desc else Row.row_index > idx
    pair = tuple_(expr, Row.row_index)
    target = tuple_(literal(value), literal(idx))
    return pair < target if desc else pair > target


__all__ = ["RowFilter", "SortField", "encode_cursor", "keyset_condition", "or_", "sort_expr"]
