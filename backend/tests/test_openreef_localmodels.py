"""Tests for the OpenReef local-model adapter.

The adapter must: detect local endpoints (auto), leave hosted endpoints
untouched, and apply the proven shim transforms (root-object JSON schema,
token cap, repetition penalty). Explicit OPENREEF_LOCAL_LLM=on|off
overrides the heuristic.
"""

from types import SimpleNamespace

import pytest

from app.openreef.localmodels import (
    MAX_TOKENS_CAP,
    MODE_ENV,
    REP_PENALTY,
    ROOT_OBJECT_RESPONSE_FORMAT,
    apply_local_model_adaptations,
    local_llm_mode,
    maybe_adapt_for_client,
    reset_announcement_flag,
)
from app.utils.openai_chat_compat import create_chat_completion


@pytest.fixture(autouse=True)
def clean_mode_env(monkeypatch):
    monkeypatch.delenv(MODE_ENV, raising=False)
    reset_announcement_flag()
    yield
    reset_announcement_flag()


# ── detection ───────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://localhost:8000/v1",
    "http://127.0.0.1:8000/v1",
    "http://[::1]:8000/v1",
    "http://host.container.internal:8000/v1",   # Apple containers → host
    "http://host.docker.internal:8000/v1",      # Docker Desktop → host
    "http://myserver.local:8000/v1",
    "http://nas.lan:8000/v1",
])
def test_auto_detects_local_endpoints(url):
    assert local_llm_mode(url) is True


@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1",
    "https://api.cloudflare.com/client/v4/accounts/x/ai/v1",
    "https://llm.internal-hosting.example/v1",  # real DNS name, not local
    "",
    None,
])
def test_auto_ignores_hosted_endpoints(url):
    assert local_llm_mode(url) is False


def test_explicit_override_on_beats_hosted_url(monkeypatch):
    monkeypatch.setenv(MODE_ENV, "on")
    assert local_llm_mode("https://api.openai.com/v1") is True


def test_explicit_override_off_beats_local_url(monkeypatch):
    monkeypatch.setenv(MODE_ENV, "off")
    assert local_llm_mode("http://localhost:8000/v1") is False


# ── transforms ──────────────────────────────────────────────────────

def test_json_object_becomes_root_object_schema():
    out = apply_local_model_adaptations({
        "model": "m",
        "messages": [],
        "response_format": {"type": "json_object"},
    })
    assert out["response_format"] == ROOT_OBJECT_RESPONSE_FORMAT
    assert out["response_format"]["json_schema"]["schema"] == {"type": "object"}


def test_non_json_object_response_format_untouched():
    rf = {"type": "json_schema", "json_schema": {"name": "x", "schema": {"type": "object"}}}
    out = apply_local_model_adaptations({"response_format": rf})
    assert out["response_format"] == rf


def test_max_tokens_capped_only_when_missing_or_higher():
    assert apply_local_model_adaptations({})["max_tokens"] == MAX_TOKENS_CAP
    assert apply_local_model_adaptations({"max_tokens": 32768})["max_tokens"] == MAX_TOKENS_CAP
    assert apply_local_model_adaptations({"max_tokens": 2048})["max_tokens"] == 2048


def test_repetition_penalty_added_once():
    out = apply_local_model_adaptations({})
    assert out["repetition_penalty"] == REP_PENALTY
    out2 = apply_local_model_adaptations(out)
    assert out2["repetition_penalty"] == REP_PENALTY
    kept = apply_local_model_adaptations({"repetition_penalty": 1.2})
    assert kept["repetition_penalty"] == 1.2


# ── compat-layer hook ───────────────────────────────────────────────

class _Recorder:
    def __init__(self, base_url):
        self.base_url = base_url
        self.kwargs = None
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])


def test_hook_adapts_local_client():
    client = _Recorder("http://host.container.internal:8000/v1")
    create_chat_completion(
        client,
        model="Qwen3.5-4B-MLX-4bit",
        messages=[],
        max_tokens=32768,
        response_format={"type": "json_object"},
    )
    assert client.kwargs["response_format"] == ROOT_OBJECT_RESPONSE_FORMAT
    assert client.kwargs["max_tokens"] == MAX_TOKENS_CAP
    assert client.kwargs["repetition_penalty"] == REP_PENALTY


def test_hook_passes_through_hosted_client():
    client = _Recorder("https://api.openai.com/v1")
    create_chat_completion(
        client,
        model="gpt-4.1-mini",
        messages=[],
        max_tokens=4096,
        response_format={"type": "json_object"},
    )
    assert client.kwargs["response_format"] == {"type": "json_object"}
    assert "repetition_penalty" not in client.kwargs


def test_hook_respects_explicit_off(monkeypatch):
    monkeypatch.setenv(MODE_ENV, "off")
    client = _Recorder("http://localhost:8000/v1")
    create_chat_completion(
        client,
        model="local-model",
        messages=[],
        response_format={"type": "json_object"},
    )
    assert client.kwargs["response_format"] == {"type": "json_object"}


def test_maybe_adapt_announces_once(caplog):
    import logging

    client = _Recorder("http://localhost:8000/v1")
    with caplog.at_level(logging.INFO, logger="app.openreef.localmodels"):
        maybe_adapt_for_client(client, {"model": "m"})
        maybe_adapt_for_client(client, {"model": "m"})
    info_lines = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(info_lines) == 1
    assert "local-model mode" in info_lines[0].getMessage()
