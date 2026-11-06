"""AI repair (T2) and disambiguation (T3).

Only the mapped address fields plus an opaque id are ever sent. Every AI suggestion is re-run
through the deterministic normalizer and discarded unless the gazetteer confirms it."""

import json
from dataclasses import dataclass, field

from app.ai.client import AIClient, AIError, AIRequest
from app.gazetteer.base import Gazetteer
from app.geocode.base import GeoCandidate
from app.normalize.pipeline import Correction, NormResult, RawAddress, normalize
from app.normalize.postcode import compact
from app.normalize.text import fold

ADDRESS_FIELDS = ("street", "house_number", "city", "postcode", "country")

REPAIR_SYSTEM = """You repair messy postal address fragments so that a gazetteer lookup can succeed.
For each input item return the corrected fields:
- country: ISO 3166-1 alpha-2 code (e.g. "DE"). Fix corrupted codes, names in any language, typos.
- postcode: the postcode in the country's official format (restore lost leading zeros, fix spacing).
- city: the official local name of the city/town (fix typos, expand abbreviations like "a.M." or "St.").
- street / house_number: fix obvious typos and split a house number out of the street if combined.
Move values that are in the wrong field to the right one.
Never invent data: if a field cannot be determined with good certainty, return it unchanged (or empty
if it was empty). List the names of fields you changed in changed_fields and give a short reason.
Do not output coordinates."""

REPAIR_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    **{f: {"type": "string"} for f in ADDRESS_FIELDS},
                    "changed_fields": {"type": "array", "items": {"type": "string", "enum": list(ADDRESS_FIELDS)}},
                    "reason": {"type": "string"},
                },
                "required": ["id", *ADDRESS_FIELDS, "changed_fields", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

DISAMBIGUATE_SYSTEM = """You pick which candidate location best matches a postal address input.
Use all input fields. Answer with the id of exactly one candidate, or "none" if no candidate clearly
matches. Give a short reason. Do not output coordinates."""


@dataclass
class AIRepair:
    address: RawAddress
    changed_fields: list[str] = field(default_factory=list)
    reason: str = ""


def payload_item(opaque_id: str, raw: RawAddress) -> dict:
    """The complete set of data that leaves the server for one address."""
    return {"id": opaque_id, **{f: getattr(raw, f) for f in ADDRESS_FIELDS}}


def repair_request(custom_id: str, items: list[tuple[str, RawAddress]]) -> AIRequest:
    payload = [payload_item(i, raw) for i, raw in items]
    return AIRequest(custom_id, REPAIR_SYSTEM, json.dumps({"items": payload}, ensure_ascii=False), "address_repair",
                     REPAIR_SCHEMA)  # fmt: skip


def parse_repair_response(data: dict, expected_ids: set[str]) -> dict[str, AIRepair]:
    out: dict[str, AIRepair] = {}
    for item in data.get("items", []):
        rid = str(item.get("id", ""))
        if rid not in expected_ids:
            continue
        addr = RawAddress(**{f: str(item.get(f, "") or "") for f in ADDRESS_FIELDS})
        changed = [f for f in item.get("changed_fields", []) if f in ADDRESS_FIELDS]
        out[rid] = AIRepair(addr, changed, str(item.get("reason", ""))[:500])
    return out


async def repair_addresses(ai: AIClient, items: list[tuple[str, RawAddress]]) -> dict[str, AIRepair]:
    """Synchronous (non-batch) repair of one group of addresses."""
    data = await ai.complete_json(repair_request("sync", items))
    return parse_repair_response(data, {i for i, _ in items})


def _changed(original: RawAddress, repaired: RawAddress) -> list[str]:
    out = []
    for f in ADDRESS_FIELDS:
        a, b = getattr(original, f).strip(), getattr(repaired, f).strip()
        same = compact(a) == compact(b) if f == "postcode" else fold(a) == fold(b)
        if not same:
            out.append(f)
    return out


async def validate_repair(
    original: RawAddress, rep: AIRepair, gaz: Gazetteer, default_country: str | None
) -> tuple[NormResult | None, str]:
    """Re-run the deterministic tier on the AI output. Returns (result, reason) - result is None
    when the suggestion failed validation."""
    original = original.cleaned()
    changed = _changed(original, rep.address.cleaned())
    if not changed:
        return None, "AI made no usable change"
    n = await normalize(rep.address, gaz, default_country, tier="ai")
    problems = []
    if n.resolution != "resolved":
        problems.append("result still unresolved")
    if "postcode" in changed and n.postcode_found not in ("found", "unverifiable"):
        problems.append(f"postcode {rep.address.postcode} does not exist in {n.country}")
    if "city" in changed and n.city_match not in ("exact", "alias", "fuzzy", "from_postcode"):
        problems.append(f"city {rep.address.city} not found in {n.country}")
    if "country" in changed and n.country != rep.address.country.strip().upper():
        problems.append("country could not be confirmed")
    if problems:
        return None, "AI suggestion failed validation: " + "; ".join(problems)

    ai_corrections = [
        Correction(f, getattr(original, f), getattr(rep.address, f).strip(), "ai", rep.reason or "AI repair")
        for f in changed
    ]
    # Deterministic corrections relative to the AI values that only re-format them are noise.
    det = [c for c in n.corrections if not (c.field in changed and c.reason in ("alias", "canonical formatting"))]
    n.corrections = ai_corrections + det
    if "ai_repaired" not in n.flags:
        n.flags.append("ai_repaired")
    return n, rep.reason


def disambiguation_request(raw: RawAddress, cands: list[GeoCandidate]) -> AIRequest:
    ids = [f"c{i + 1}" for i in range(len(cands))]
    payload = {
        "input": {f: getattr(raw, f) for f in ADDRESS_FIELDS},
        "candidates": [
            {"id": cid, "label": c.label, "precision": c.precision,
             "components": {k: v for k, v in c.components.items() if k in ("city", "postcode", "state", "county",
                                                                            "country", "road", "suburb")}}
            for cid, c in zip(ids, cands, strict=True)
        ],
    }  # fmt: skip
    schema = {
        "type": "object",
        "properties": {"choice": {"type": "string", "enum": [*ids, "none"]}, "reason": {"type": "string"}},
        "required": ["choice", "reason"],
        "additionalProperties": False,
    }
    return AIRequest("disambiguate", DISAMBIGUATE_SYSTEM, json.dumps(payload, ensure_ascii=False), "pick_candidate",
                     schema)  # fmt: skip


async def disambiguate(ai: AIClient, raw: RawAddress, cands: list[GeoCandidate]) -> tuple[int | None, str]:
    """Index of the chosen candidate (never coordinates from the AI), or None."""
    try:
        data = await ai.complete_json(disambiguation_request(raw, cands))
    except AIError as e:
        return None, str(e)
    choice = str(data.get("choice", "none"))
    if choice.startswith("c") and choice[1:].isdigit() and 1 <= int(choice[1:]) <= len(cands):
        return int(choice[1:]) - 1, str(data.get("reason", ""))[:500]
    return None, str(data.get("reason", ""))[:500]
