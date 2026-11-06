"""Request/response models for the distance calculator."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.normalize.country import resolve_country

Precision = Literal["rooftop", "street", "postcode", "city", "region", "country", "manual"]
ADDRESS_FIELDS = ("street", "house_number", "postcode", "city", "region", "country", "text")
KM_PER_MI = 1.609344
Short = Annotated[str, Field(max_length=20)]
Medium = Annotated[str, Field(max_length=100)]
Long = Annotated[str, Field(max_length=300)]


class AddressInput(BaseModel):
    """Any combination of postal-address parts and/or one free-text line."""

    type: Literal["address"] = "address"
    street: Long = ""
    house_number: Short = ""
    postcode: Short = ""
    city: Medium = ""
    region: Medium = ""
    country: Medium = ""
    text: Long = ""

    @field_validator(*ADDRESS_FIELDS, mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return " ".join(v.split()) if isinstance(v, str) else ("" if v is None else v)

    @field_validator("country")
    @classmethod
    def _country_iso2(cls, v: str) -> str:
        if not v:
            return ""
        code = resolve_country(v).code
        if code is None:
            raise ValueError(f"unknown country: {v!r}")
        return code

    @model_validator(mode="after")
    def _not_empty(self) -> "AddressInput":
        if not any(getattr(self, f) for f in ADDRESS_FIELDS):
            raise ValueError("enter at least one address field")
        return self


class CoordsInput(BaseModel):
    """Decimal coordinates. `label`/`precision`/`place_id` are set when the user picked a Google alternative."""

    type: Literal["coords"] = "coords"
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    label: str | None = Field(default=None, max_length=300)
    precision: Precision | None = None
    place_id: str | None = Field(default=None, max_length=300)


LocationInput = Annotated[AddressInput | CoordsInput, Field(discriminator="type")]


class DistanceRequest(BaseModel):
    origin: LocationInput
    destination: LocationInput
    road: bool = True


class Alternative(BaseModel):
    label: str
    lat: float
    lon: float
    precision: Precision
    place_id: str | None = None
    partial_match: bool = False


class ResolvedLocation(BaseModel):
    label: str
    lat: float
    lon: float
    source: Literal["google", "input"]
    precision: Precision
    place_id: str | None = None
    partial_match: bool = False
    alternatives: list[Alternative] = Field(default_factory=list)


class Distance(BaseModel):
    km: float
    mi: float

    @classmethod
    def from_km(cls, km: float) -> "Distance":
        return cls(km=round(km, 3), mi=round(km / KM_PER_MI, 3))


class RoadDistance(Distance):
    duration_s: int
    polyline: str | None = None


RoadUnavailable = Literal["disabled", "no_route", "provider_error", "same_point"]


class DistanceResponse(BaseModel):
    origin: ResolvedLocation
    destination: ResolvedLocation
    straight_line: Distance
    road: RoadDistance | None = None
    road_distance_unavailable: RoadUnavailable | None = None
    attribution: list[str] = Field(default_factory=list)
