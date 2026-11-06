"""AI column-mapping suggestion: only headers and at most 5 sample rows are sent."""

import json

from app.ai.client import AIClient, AIError, AIRequest
from app.ingest.mapping import TARGET_FIELDS

MAX_SAMPLE_ROWS = 5

SYSTEM = """You map the columns of an address spreadsheet to target fields.
Target fields: street, house_number, city, postcode, country, id (a record identifier), full_address
(a single column containing a complete address). For each target field return the exact name of the
column that holds it, or null if no column does. Never map one column to two fields."""


async def ai_suggest_mapping(ai: AIClient, columns: list[str], rows: list[list[str]]) -> dict[str, str | None]:
    sample = [dict(zip(columns, r, strict=False)) for r in rows[:MAX_SAMPLE_ROWS]]
    schema = {
        "type": "object",
        "properties": {f: {"anyOf": [{"type": "string", "enum": columns}, {"type": "null"}]} for f in TARGET_FIELDS},
        "required": list(TARGET_FIELDS),
        "additionalProperties": False,
    }
    req = AIRequest("mapping", SYSTEM, json.dumps({"columns": columns, "sample_rows": sample}, ensure_ascii=False),
                    "column_mapping", schema)  # fmt: skip
    try:
        data = await ai.complete_json(req)
    except AIError:
        return dict.fromkeys(TARGET_FIELDS)
    out: dict[str, str | None] = {}
    used: set[str] = set()
    for f in TARGET_FIELDS:
        col = data.get(f)
        out[f] = col if isinstance(col, str) and col in columns and col not in used else None
        if out[f]:
            used.add(out[f])
    return out
