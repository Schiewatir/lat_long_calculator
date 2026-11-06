"""OpenAI client against an Azure-style gateway (URL shape, auth header, api-version, fallback)."""

import json

import httpx2

from app.ai.client import AIRequest, OpenAIClient
from app.config import Settings

GW = "https://gateway.example.com/openai"
URL = f"{GW}/deployments/gpt-5.1/chat/completions"
SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"], "additionalProperties": False}
REQ = AIRequest("x", "system prompt", '{"items": []}', "test", SCHEMA)


def gateway_settings(**kw) -> Settings:
    base = dict(openai_api_key="secret-key", openai_base_url=GW, openai_api_version="2024-02-01",
                openai_chat_deployment="gpt-5.1", openai_auth_style="api-key", openai_timeout_s=5)  # fmt: skip
    base.update(kw)
    return Settings(_env_file=None, **base)


class Gateway:
    """Mock transport for the SDK's bundled HTTP client; records requests, replays responses."""

    def __init__(self, *responses: httpx2.Response):
        self.responses = list(responses)
        self.calls: list[httpx2.Request] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.calls.append(request)
        assert str(request.url).startswith(URL)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    def client(self, **kw) -> OpenAIClient:
        return OpenAIClient(gateway_settings(**kw), http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler)))


def ok(content: dict) -> httpx2.Response:
    return httpx2.Response(200, json={"id": "1", "object": "chat.completion", "created": 0, "model": "gpt-5.1",
                                     "choices": [{"index": 0, "finish_reason": "stop",
                                                  "message": {"role": "assistant", "content": json.dumps(content)}}]})  # fmt: skip


async def test_api_key_style_request_shape():
    gw = Gateway(ok({"a": "b"}))
    assert await gw.client().complete_json(REQ) == {"a": "b"}
    req = gw.calls[-1]
    assert req.url.params["api-version"] == "2024-02-01"
    assert req.headers["api-key"] == "secret-key"
    assert "secret-key" not in req.headers.get("authorization", "")
    body = json.loads(req.content)
    assert body["model"] == "gpt-5.1" and body["response_format"]["type"] == "json_schema"
    assert "temperature" not in body


async def test_subscription_key_and_bearer_styles():
    gw = Gateway(ok({"a": "b"}))
    await gw.client(openai_auth_style="subscription-key").complete_json(REQ)
    assert gw.calls[-1].headers["ocp-apim-subscription-key"] == "secret-key"
    await gw.client(openai_auth_style="bearer").complete_json(REQ)
    assert gw.calls[-1].headers["authorization"] == "Bearer secret-key"


async def test_falls_back_to_json_mode_when_schema_rejected():
    rejected = httpx2.Response(400, json={"error": {"message": "response_format value as json_schema is enabled only "
                                                              "for api versions 2024-08-01-preview and later",
                                                   "type": "invalid_request_error"}})  # fmt: skip
    gw = Gateway(rejected, ok({"a": "c"}), ok({"a": "d"}))
    client = gw.client()
    assert await client.complete_json(REQ) == {"a": "c"}
    second = json.loads(gw.calls[1].content)
    assert second["response_format"] == {"type": "json_object"} and "JSON schema" in second["messages"][0]["content"]
    await client.complete_json(REQ)  # stays in JSON mode, no second rejection round-trip
    assert len(gw.calls) == 3


def test_settings_from_env_file_with_inline_comments(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "OPENAI_BASE_URL=https://gw.example.com/openai\n"
        "OPENAI_CHAT_DEPLOYMENT=gpt-5.1             # deployment/model name\n"
        "OPENAI_EMBEDDING_DEPLOYMENT=         # optional\n"
        "OPENAI_API_KEY=k\n"
        "OPENAI_AUTH_STYLE=api-key            # api-key | bearer | subscription-key\n"
        "OPENAI_TIMEOUT_S=30\n"
    )
    import os

    saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("OPENAI_")}
    try:
        s = Settings(_env_file=env)
    finally:
        os.environ.update(saved)
    assert s.chat_model == "gpt-5.1" and s.openai_embedding_deployment is None
    assert s.openai_auth_style == "api-key" and s.openai_timeout_s == 30
    assert s.openai_gateway and not s.ai_batch_supported and s.ai_available
