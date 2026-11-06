import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Protocol

PRECISION_RANK = {"rooftop": 0, "street": 1, "postcode": 2, "city": 3, "region": 4, "country": 5, "none": 6}


@dataclass
class GeoCandidate:
    lat: float
    lon: float
    source: str  # geonames_postcode | geonames_city | provider:<name> | manual
    precision: str  # rooftop | street | postcode | city | region | country | manual
    label: str = ""
    components: dict = field(default_factory=dict)
    provider_confidence: float | None = None
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GeoCandidate":
        return cls(**d)


@dataclass(frozen=True)
class ProviderQuery:
    street: str = ""
    house_number: str = ""
    postcode: str = ""
    city: str = ""
    country: str | None = None

    def text(self, country_label: str = "") -> str:
        street = " ".join(x for x in (self.street, self.house_number) if x)
        locality = " ".join(x for x in (self.postcode, self.city) if x)
        return ", ".join(x for x in (street, locality, country_label) if x)

    def cache_key(self) -> str:
        payload = json.dumps(
            {k: (v or "").strip().lower() for k, v in asdict(self).items()}, sort_keys=True, ensure_ascii=False
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class ProviderError(Exception):
    def __init__(self, message: str, *, transient: bool = False, quota: bool = False):
        super().__init__(message)
        self.transient = transient
        self.quota = quota


class GeocoderProvider(Protocol):
    name: str

    async def geocode(self, query: ProviderQuery) -> list[GeoCandidate]: ...
