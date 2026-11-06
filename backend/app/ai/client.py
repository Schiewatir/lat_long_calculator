"""Thin OpenAI wrapper. Structured Outputs only; schemas never contain coordinate fields."""

import io
import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from app.config import Settings

log = logging.getLogger(__name__)


class AIError(Exception):
    pass


@dataclass
class AIRequest:
    custom_id: str
    system: str
    user: str
    schema_name: str
    schema: dict


class AIClient(Protocol):
    async def complete_json(self, req: AIRequest) -> dict: ...

    async def submit_batch(self, requests: list[AIRequest]) -> str: ...

    async def batch_results(self, batch_id: str) -> tuple[str, dict[str, dict] | None]:
        """(status, results by custom_id once completed)."""
        ...


class OpenAIClient:
    def __init__(self, settings: Settings, http_client: Any = None):
        from openai import AsyncOpenAI

        key = settings.openai_api_key or ""
        kwargs: dict[str, Any] = {"max_retries": 4, "timeout": settings.openai_timeout_s}
        if settings.openai_gateway:
            # Azure-style gateway: {base}/deployments/{deployment}/chat/completions?api-version=...
            base = settings.openai_base_url.rstrip("/")  # type: ignore[union-attr]
            kwargs["base_url"] = f"{base}/deployments/{settings.chat_model}"
            if settings.openai_api_version:
                kwargs["default_query"] = {"api-version": settings.openai_api_version}
        if settings.openai_auth_style == "bearer":
            kwargs["api_key"] = key
        else:
            # The key goes only in the gateway's own header; the SDK's bearer slot gets a placeholder.
            header = "api-key" if settings.openai_auth_style == "api-key" else "Ocp-Apim-Subscription-Key"
            kwargs["api_key"] = "unused"
            kwargs["default_headers"] = {header: key}
        if http_client is not None:
            kwargs["http_client"] = http_client
        self.client = AsyncOpenAI(**kwargs)
        self.model = settings.chat_model
        self.temperature = settings.openai_temperature
        self.structured = True  # flips to JSON mode if the endpoint rejects json_schema

    def _body(self, req: AIRequest) -> dict[str, Any]:
        system = req.system
        if self.structured:
            fmt: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {"name": req.schema_name, "schema": req.schema, "strict": True},
            }
        else:
            # JSON mode fallback; the output is validated locally exactly as in structured mode.
            system += "\n\nRespond only with a JSON object that conforms to this JSON schema:\n" + json.dumps(req.schema)
            fmt = {"type": "json_object"}
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": req.user}],
            "response_format": fmt,
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        return body

    async def complete_json(self, req: AIRequest) -> dict:
        from openai import BadRequestError

        try:
            try:
                resp = await self.client.chat.completions.create(**self._body(req))
            except BadRequestError as e:
                if not self.structured or ("json_schema" not in str(e) and "response_format" not in str(e)):
                    raise
                log.warning("endpoint rejected structured outputs; falling back to JSON mode")
                self.structured = False
                resp = await self.client.chat.completions.create(**self._body(req))
        except Exception as e:  # noqa: BLE001 - surface any SDK error as AIError
            log.warning("OpenAI request failed: %s", type(e).__name__)
            raise AIError(f"OpenAI request failed: {type(e).__name__}") from e
        content = resp.choices[0].message.content or ""
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise AIError("OpenAI returned invalid JSON") from e

    async def submit_batch(self, requests: list[AIRequest]) -> str:
        lines = [
            json.dumps({"custom_id": r.custom_id, "method": "POST", "url": "/v1/chat/completions", "body": self._body(r)})
            for r in requests
        ]
        data = io.BytesIO("\n".join(lines).encode())
        f = await self.client.files.create(file=("repair.jsonl", data), purpose="batch")
        batch = await self.client.batches.create(
            input_file_id=f.id, endpoint="/v1/chat/completions", completion_window="24h"
        )
        return batch.id

    async def batch_results(self, batch_id: str) -> tuple[str, dict[str, dict] | None]:
        batch = await self.client.batches.retrieve(batch_id)
        if batch.status in ("validating", "in_progress", "finalizing", "cancelling"):
            return batch.status, None
        if batch.status != "completed":
            return batch.status, {}
        results: dict[str, dict] = {}
        if batch.output_file_id:
            content = await self.client.files.content(batch.output_file_id)
            for line in content.text.splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                try:
                    body = item["response"]["body"]
                    results[item["custom_id"]] = json.loads(body["choices"][0]["message"]["content"])
                except (KeyError, TypeError, IndexError, json.JSONDecodeError):
                    log.warning("unusable batch result for %s", item.get("custom_id"))
        return "completed", results


def build_ai_client(settings: Settings) -> AIClient | None:
    return OpenAIClient(settings) if settings.ai_available else None
