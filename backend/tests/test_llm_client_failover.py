"""Failover tests for LLMClient's provider chains.

With no registry configured there is a single chain entry, so behavior
must be identical to upstream (errors propagate, no failover attempts).
With a registry, provider-level errors fail over to the next entry.
"""

import json
from types import SimpleNamespace

import pytest

from app.config import Config
from app.utils.llm_client import LLMClient, LLMResponseError, _is_failoverable
from app.utils.llm_providers import clear_registry_cache


@pytest.fixture(autouse=True)
def clean_registry_env(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDERS_JSON", raising=False)
    monkeypatch.delenv("LLM_PROVIDERS_FILE", raising=False)
    clear_registry_cache()
    yield
    clear_registry_cache()


def fake_response(text, finish_reason="stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=finish_reason)]
    )


class FakeStatusError(Exception):
    """Stand-in for openai.APIStatusError subclasses (duck-typed on status_code)."""

    def __init__(self, status_code, message="boom"):
        super().__init__(message)
        self.status_code = status_code


REGISTRY = {
    "providers": [
        {"name": "primary", "base_url": "https://p.example.com", "api_key": "pk", "model": "pm"},
        {"name": "backup", "base_url": "https://b.example.com", "api_key": "bk", "model": "bm"},
    ],
    "chains": {"default": ["primary", "backup"], "report": ["primary", "backup"]},
}


def use_registry(monkeypatch, payload=REGISTRY):
    monkeypatch.setenv("LLM_PROVIDERS_JSON", json.dumps(payload))
    clear_registry_cache()


# ── _is_failoverable ────────────────────────────────────────────────

def test_content_errors_are_not_failoverable():
    assert not _is_failoverable(LLMResponseError("bad json"))


def test_generic_value_errors_are_not_failoverable():
    assert not _is_failoverable(ValueError("nope"))


@pytest.mark.parametrize("status", [429, 500, 503, 401, 408])
def test_status_codes_are_failoverable(status):
    assert _is_failoverable(FakeStatusError(status))


def test_bad_request_is_not_failoverable():
    # 400 without the response_format signature is a request problem, not
    # a provider-health problem — must surface as today.
    assert not _is_failoverable(FakeStatusError(400))


def test_transport_errors_are_failoverable():
    assert _is_failoverable(ConnectionError("reset"))
    assert _is_failoverable(TimeoutError("slow"))
    assert _is_failoverable(OSError("down"))


# ── single-provider behavior (no registry) ──────────────────────────

def test_explicit_credentials_single_chain():
    client = LLMClient(api_key="k", base_url="u", model="m")
    assert len(client._chain) == 1
    assert client._chain[0].provider == "explicit"


def test_missing_api_key_raises_as_before(monkeypatch):
    # The repo may contain a real .env (dotenv override loads it into Config),
    # so pin the value instead of relying on an unset environment.
    monkeypatch.setattr(Config, "LLM_API_KEY", None)
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        LLMClient(api_key=None, base_url="u", model="m")


def test_last_provider_error_propagates(monkeypatch):
    client = LLMClient(api_key="k", base_url="u", model="m")
    calls = {"n": 0}

    def boom(entry, **kwargs):
        calls["n"] += 1
        raise FakeStatusError(503)

    monkeypatch.setattr(client, "_create_completion", boom)
    with pytest.raises(FakeStatusError):
        client.chat([{"role": "user", "content": "hi"}])
    assert calls["n"] == 1  # single chain: no retry, no failover


# ── failover across a registry chain ────────────────────────────────

def test_chat_fails_over_on_provider_error(monkeypatch):
    use_registry(monkeypatch)
    client = LLMClient(stage="report")
    assert [e.provider for e in client._chain] == ["primary", "backup"]

    seen = []

    def flaky(entry, **kwargs):
        seen.append(entry.provider)
        if entry.provider == "primary":
            raise FakeStatusError(429)
        return fake_response("hello from backup")

    monkeypatch.setattr(client, "_create_completion", flaky)
    assert client.chat([{"role": "user", "content": "hi"}]) == "hello from backup"
    assert seen == ["primary", "backup"]


def test_chat_json_fails_over_on_garbage_output(monkeypatch):
    use_registry(monkeypatch)
    client = LLMClient(stage="report")

    def flaky(entry, **kwargs):
        if entry.provider == "primary":
            return fake_response("not json at all")
        return fake_response('{"title": "Backup Report", "sections": []}')

    monkeypatch.setattr(client, "_create_completion", flaky)
    result = client.chat_json([{"role": "user", "content": "outline"}])
    assert result["title"] == "Backup Report"


def test_chat_json_keeps_negotiating_json_mode_per_provider(monkeypatch):
    """response_format downgrade is per-provider; the backup gets a fresh start."""

    class FormatRejected(Exception):
        status_code = 400

        def __init__(self):
            super().__init__("unsupported parameter: response_format")
            self.body = {
                "error": {
                    "param": "response_format",
                    "code": "unsupported_parameter",
                    "message": "unsupported parameter: response_format",
                }
            }

    use_registry(monkeypatch)
    client = LLMClient(stage="report")

    def flaky(entry, response_format=None, **kwargs):
        if entry.provider == "primary":
            if response_format is not None:
                raise FormatRejected()
            # Downgraded request goes through but returns unusable content;
            # with max_attempts=1 the entry is exhausted and we fail over.
            return fake_response("garbage")
        # Backup receives a fresh json_object attempt.
        assert response_format == {"type": "json_object"}
        return fake_response('{"ok": true}')

    monkeypatch.setattr(client, "_create_completion", flaky)
    assert client.chat_json([{"role": "user", "content": "json please"}]) == {"ok": True}


def test_failover_gives_up_after_last_provider(monkeypatch):
    use_registry(monkeypatch)
    client = LLMClient(stage="report")
    calls = {"n": 0}

    def boom(entry, **kwargs):
        calls["n"] += 1
        raise FakeStatusError(500)

    monkeypatch.setattr(client, "_create_completion", boom)
    with pytest.raises(FakeStatusError):
        client.chat_json([{"role": "user", "content": "hi"}], max_attempts=2)
    # 2 providers, provider-level error → one attempt each.
    assert calls["n"] == 2


def test_no_registry_no_failover_for_legacy(monkeypatch):
    from app.config import Config

    monkeypatch.setattr(Config, "LLM_API_KEY", "legacy-key")
    client = LLMClient(stage="report")
    assert [e.provider for e in client._chain] == ["legacy"]
