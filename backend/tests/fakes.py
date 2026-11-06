import json

from app.ai.client import AIRequest


class FakeAI:
    """Records every request; answers with a callable or canned responses."""

    def __init__(self, responder=None):
        self.requests: list[AIRequest] = []
        self.responder = responder
        self.batches: dict[str, list[AIRequest]] = {}
        self.batch_status = "completed"

    async def complete_json(self, req: AIRequest) -> dict:
        self.requests.append(req)
        return self.responder(req) if self.responder else {}

    async def submit_batch(self, requests: list[AIRequest]) -> str:
        self.requests.extend(requests)
        bid = f"batch_{len(self.batches) + 1}"
        self.batches[bid] = requests
        return bid

    async def batch_results(self, batch_id: str):
        if self.batch_status != "completed":
            return self.batch_status, None
        return "completed", {r.custom_id: self.responder(r) for r in self.batches[batch_id]}

    def all_payload_text(self) -> str:
        return "\n".join(r.user for r in self.requests)


def echo_repair(fix: dict[str, dict]):
    """Responder for repair requests: apply `fix[city_input]` field overrides to each item."""

    def respond(req: AIRequest) -> dict:
        if req.schema_name == "pick_candidate":
            return {"choice": "none", "reason": "not sure"}
        items = json.loads(req.user)["items"]
        out = []
        for it in items:
            patch = fix.get(it["city"], {})
            new = {**it, **{k: v for k, v in patch.items() if k != "reason"}}
            changed = [k for k in ("street", "house_number", "city", "postcode", "country") if new[k] != it[k]]
            out.append({**new, "changed_fields": changed, "reason": patch.get("reason", "fixed")})
        return {"items": out}

    return respond
