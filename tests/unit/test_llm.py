import json

import httpx
import pytest

from sqlsentry.config import FewShotExample, ProviderConfig
from sqlsentry.errors import ConfigError, LLMError
from sqlsentry.llm import FakeProvider, build_provider
from sqlsentry.llm.base import LLMProvider, LLMResult
from sqlsentry.llm.openai_compatible import OpenAICompatibleProvider
from sqlsentry.llm.parsing import ParseError, parse_candidate
from sqlsentry.prompts import build_prompt, repair_messages
from sqlsentry.types import CANDIDATE_JSON_SCHEMA

GOOD = {
    "sql": "SELECT 1",
    "explanation": "x",
    "tables_used": [],
    "assumptions": [],
    "needs_clarification": False,
    "clarification_question": None,
}


# ---------- parsing ----------


def test_parse_plain_json():
    assert parse_candidate(json.dumps(GOOD)).sql == "SELECT 1"


def test_parse_fenced_json_with_reasoning_and_prose():
    text = f"<think>hmm</think>Sure! Here you go:\n```json\n{json.dumps(GOOD)}\n```\nHope it helps"
    assert parse_candidate(text).sql == "SELECT 1"


def test_parse_partial_json_uses_defaults():
    c = parse_candidate('{"sql": "SELECT 2"}')
    assert c.sql == "SELECT 2" and c.needs_clarification is False


def test_parse_bare_sql_fence_fallback():
    c = parse_candidate("```sql\nSELECT 3\n```")
    assert c.sql == "SELECT 3" and c.assumptions


@pytest.mark.parametrize("text", ["", "I cannot help", '{"sql": 5, "needs_clarification": "maybe"}'])
def test_parse_failures(text):
    with pytest.raises(ParseError):
        parse_candidate(text)


# ---------- prompts ----------


def test_prompt_contains_only_filtered_schema(store_catalog):
    p = build_prompt(
        question="top customers",
        catalog=store_catalog,
        dialect="sqlite",
        datasource="store",
        glossary=["revenue = quantity * unit_price"],
        examples=[FewShotExample(question="count orders", sql="SELECT COUNT(*) FROM orders")],
        other_tables=["archive"],
    )
    user = p.messages[0]["content"]
    assert "TABLE customers" in user and "FOREIGN KEY (customer_id) REFERENCES customers(id)" in user
    assert "email" not in user and "internal_audit_log" not in user
    assert "revenue = quantity" in user and "count orders" in user and "archive" in user
    assert user.rstrip().endswith("Question: top customers")
    assert "sqlite" in p.system and '"needs_clarification"' in p.system


def test_system_prompt_is_stable_across_questions(store_catalog):
    a = build_prompt(question="a", catalog=store_catalog, dialect="sqlite", datasource="store")
    b = build_prompt(question="b", catalog=store_catalog, dialect="sqlite", datasource="store")
    assert a.system == b.system


def test_repair_messages():
    msgs = repair_messages("raw", "column x does not exist")
    assert [m["role"] for m in msgs] == ["assistant", "user"]
    assert "column x does not exist" in msgs[1]["content"]


# ---------- providers ----------


def test_fake_provider_scripts_and_records():
    fake = FakeProvider([GOOD, lambda s, m: {"sql": "SELECT 2"}])
    assert json.loads(fake.complete("sys", [{"role": "user", "content": "q"}], {}).text)["sql"] == "SELECT 1"
    assert json.loads(fake.complete("sys", [], {}).text)["sql"] == "SELECT 2"
    assert len(fake.calls) == 2
    with pytest.raises(LLMError):
        fake.complete("sys", [], {})


def _openai_provider(handler, **cfg) -> OpenAICompatibleProvider:
    p = OpenAICompatibleProvider(
        "test", ProviderConfig(type="openai_compatible", base_url="http://llm.test/v1", model="m", **cfg)
    )
    p._client = httpx.Client(base_url="http://llm.test/v1", transport=httpx.MockTransport(handler))
    return p


def _ok(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "m-1",
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        },
    )


def test_openai_compatible_request_and_response():
    seen = {}

    def handler(req: httpx.Request):
        seen.update(json.loads(req.content))
        return _ok(json.dumps(GOOD))

    r = _openai_provider(handler).complete("sys", [{"role": "user", "content": "q"}], CANDIDATE_JSON_SCHEMA)
    assert r.model == "m-1" and r.usage.input_tokens == 11 and r.usage.output_tokens == 7
    assert seen["messages"][0] == {"role": "system", "content": "sys"}
    assert seen["response_format"] == {"type": "json_object"} and seen["temperature"] == 0.0


def test_openai_compatible_drops_json_mode_when_unsupported():
    bodies = []

    def handler(req):
        body = json.loads(req.content)
        bodies.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "response_format is not supported"}})
        return _ok("{}")

    p = _openai_provider(handler)
    p.complete("s", [], {})
    assert "response_format" not in bodies[-1]
    p.complete("s", [], {})
    assert len(bodies) == 3  # remembered: no second rejected attempt


def test_openai_compatible_retries_then_fails(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503, json={"error": {"message": "overloaded"}})

    with pytest.raises(LLMError, match="503"):
        _openai_provider(handler, max_retries=2).complete("s", [], {})
    assert len(calls) == 3


def test_openai_compatible_requires_base_url():
    with pytest.raises(ConfigError):
        OpenAICompatibleProvider("x", ProviderConfig(type="openai_compatible", model="m"))


class CustomProvider(LLMProvider):
    def __init__(self, name, cfg):
        self.name, self.model = name, cfg.model

    def complete(self, system, messages, json_schema):
        return LLMResult(text="{}", model=self.model)


def test_registry_builds_custom_and_rejects_unknown():
    p = build_provider("c", ProviderConfig(type=f"{__name__}:CustomProvider", model="z"))
    assert isinstance(p, CustomProvider)
    assert isinstance(build_provider("f", ProviderConfig(type="fake")), FakeProvider)
    with pytest.raises(ConfigError):
        build_provider("x", ProviderConfig(type="nope"))


def test_anthropic_request_shape(monkeypatch):
    from types import SimpleNamespace

    from sqlsentry.llm.anthropic import FALLBACK_BETA, AnthropicProvider

    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            model="claude-opus-5-5",
            content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=json.dumps(GOOD))],
            usage=SimpleNamespace(input_tokens=5, output_tokens=3),
        )

    p = AnthropicProvider("claude", ProviderConfig(type="anthropic", api_key_env="NOPE_NOT_SET"))
    p._client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    r = p.complete("sys", [{"role": "user", "content": "q"}], CANDIDATE_JSON_SCHEMA)
    assert parse_candidate(r.text).sql == "SELECT 1"
    assert captured["model"] == "claude-opus-5-5"
    assert captured["fallbacks"] == "default" and captured["betas"] == [FALLBACK_BETA]
    assert captured["output_config"]["format"]["schema"] is CANDIDATE_JSON_SCHEMA
    assert captured["output_config"]["effort"] == "medium"
    assert "temperature" not in captured and "thinking" not in captured


def test_anthropic_refusal_raises():
    from types import SimpleNamespace

    from sqlsentry.llm.anthropic import AnthropicProvider

    p = AnthropicProvider("claude", ProviderConfig(type="anthropic", options={"fallbacks": False}))
    resp = SimpleNamespace(stop_reason="refusal", model="m", content=[], usage=None)
    p._client = SimpleNamespace(messages=SimpleNamespace(create=lambda **k: resp))
    with pytest.raises(LLMError, match="declined"):
        p.complete("s", [{"role": "user", "content": "q"}], {})
