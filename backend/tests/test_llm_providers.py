"""Tests for the optional multi-LLM provider registry.

The registry is opt-in via LLM_PROVIDERS_JSON / LLM_PROVIDERS_FILE; with
none configured, every stage must resolve to the legacy single provider
so existing MiroFish setups behave exactly as before.
"""

import json

import pytest

from app.utils import llm_providers
from app.utils.llm_providers import (
    ProviderRegistry,
    ProviderSpec,
    chain_head_credentials,
    clear_registry_cache,
    describe_configuration,
    get_registry,
    resolve_chain,
    subprocess_env_overlay,
)


@pytest.fixture(autouse=True)
def clean_registry_env(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDERS_JSON", raising=False)
    monkeypatch.delenv("LLM_PROVIDERS_FILE", raising=False)
    clear_registry_cache()
    yield
    clear_registry_cache()


REGISTRY = {
    "providers": [
        {
            "name": "primary",
            "base_url": "https://primary.example.com/v1",
            "api_key": "pk",
            "model": "primary-model",
        },
        {
            "name": "backup",
            "base_url": "https://backup.example.com/v1",
            "api_key": "bk",
            "model": "backup-model",
        },
        {
            "name": "disabled",
            "base_url": "https://disabled.example.com/v1",
            "api_key": "dk",
            "model": "disabled-model",
            "enabled": False,
        },
    ],
    "chains": {
        "default": ["primary", "backup"],
        "report": ["backup", "primary"],
        "simulation": ["disabled", "backup"],
    },
}


def use_json(monkeypatch, payload):
    monkeypatch.setenv("LLM_PROVIDERS_JSON", json.dumps(payload))
    clear_registry_cache()


def test_no_registry_resolves_to_legacy():
    assert get_registry() is None
    chain = resolve_chain("report")
    assert len(chain) == 1
    assert chain[0].provider == "legacy"


def test_registry_loads_from_json_env(monkeypatch):
    use_json(monkeypatch, REGISTRY)
    registry = get_registry()
    assert registry is not None
    assert set(registry.providers) == {"primary", "backup", "disabled"}


def test_stage_chain_and_default_fallback(monkeypatch):
    use_json(monkeypatch, REGISTRY)
    report = resolve_chain("report")
    assert [e.provider for e in report] == ["backup", "primary"]
    # profile has no chain → falls back to default
    profile = resolve_chain("profile")
    assert [e.provider for e in profile] == ["primary", "backup"]


def test_disabled_provider_skipped_with_chain_fallback(monkeypatch):
    use_json(monkeypatch, REGISTRY)
    sim = resolve_chain("simulation")
    assert [e.provider for e in sim] == ["backup"]


def test_unknown_chain_reference_skipped(monkeypatch):
    use_json(monkeypatch, {
        "providers": [{"name": "only", "base_url": "u", "api_key": "k", "model": "m"}],
        "chains": {"default": ["ghost", "only"]},
    })
    chain = resolve_chain("default")
    assert [e.provider for e in chain] == ["only"]


def test_invalid_json_falls_back_to_legacy(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDERS_JSON", "{not json")
    clear_registry_cache()
    assert get_registry() is None
    assert resolve_chain("default")[0].provider == "legacy"


def test_env_ref_resolution(monkeypatch):
    monkeypatch.setenv("SECRET_KEY_X", "resolved-secret")
    use_json(monkeypatch, {
        "providers": [{
            "name": "envy",
            "base_url": "u",
            "api_key": "${SECRET_KEY_X}",
            "model": "m",
        }],
        "chains": {"default": ["envy"]},
    })
    assert resolve_chain("default")[0].api_key == "resolved-secret"


def test_subprocess_env_overlay_legacy_is_empty():
    assert subprocess_env_overlay("simulation") == {}


def test_subprocess_env_overlay_uses_chain_head(monkeypatch):
    use_json(monkeypatch, REGISTRY)
    overlay = subprocess_env_overlay("simulation")
    assert overlay == {
        "LLM_API_KEY": "bk",
        "LLM_BASE_URL": "https://backup.example.com/v1",
        "LLM_MODEL_NAME": "backup-model",
    }


def test_chain_head_credentials_none_for_legacy():
    assert chain_head_credentials("report") is None


def test_chain_head_credentials_for_stage(monkeypatch):
    use_json(monkeypatch, REGISTRY)
    assert chain_head_credentials("report") == (
        "bk", "https://backup.example.com/v1", "backup-model",
    )


def test_describe_configuration_never_leaks_keys(monkeypatch):
    use_json(monkeypatch, REGISTRY)
    info = describe_configuration()
    assert info["active"] is True
    blob = json.dumps(info)
    assert "pk" not in blob
    assert "bk" not in blob
    assert "api_key" not in blob


def test_describe_configuration_inactive():
    info = describe_configuration()
    assert info["active"] is False
    assert info["providers"] == []


def test_empty_providers_list_is_inactive(monkeypatch):
    use_json(monkeypatch, {"providers": [], "chains": {}})
    assert get_registry() is None


# ── llm.config.json convention ──────────────────────────────────────

def test_default_config_file_detected(monkeypatch, tmp_path):
    config = tmp_path / "llm.config.json"
    config.write_text(json.dumps(REGISTRY), encoding="utf-8")
    monkeypatch.setattr(
        llm_providers, "_default_config_candidates", lambda: [str(config)]
    )
    clear_registry_cache()
    registry = get_registry()
    assert registry is not None
    assert "primary" in registry.providers


def test_no_config_file_anywhere_is_legacy(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_providers, "_default_config_candidates", lambda: [])
    clear_registry_cache()
    assert get_registry() is None


def test_explicit_file_env_beats_default_file(monkeypatch, tmp_path):
    default_cfg = tmp_path / "llm.config.json"
    default_cfg.write_text(json.dumps(REGISTRY), encoding="utf-8")
    explicit = tmp_path / "custom.json"
    explicit.write_text(json.dumps({
        "providers": [{"name": "custom", "base_url": "u", "api_key": "k", "model": "m"}],
        "chains": {"default": ["custom"]},
    }), encoding="utf-8")
    monkeypatch.setattr(
        llm_providers, "_default_config_candidates", lambda: [str(default_cfg)]
    )
    monkeypatch.setenv("LLM_PROVIDERS_FILE", str(explicit))
    clear_registry_cache()
    chain = resolve_chain("default")
    assert [e.provider for e in chain] == ["custom"]


def test_inline_json_beats_file_env(monkeypatch, tmp_path):
    config = tmp_path / "llm.config.json"
    config.write_text(json.dumps(REGISTRY), encoding="utf-8")
    monkeypatch.setenv("LLM_PROVIDERS_FILE", str(config))
    use_json(monkeypatch, {
        "providers": [{"name": "inline", "base_url": "u", "api_key": "k", "model": "m"}],
        "chains": {"default": ["inline"]},
    })
    assert [e.provider for e in resolve_chain("default")] == ["inline"]
