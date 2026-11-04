"""Optional multi-LLM provider registry and stage-based resolution.

OpenReef can drive several OpenAI-compatible LLM providers: different
models per pipeline stage (ontology extraction, agent profiles, sim
config, OASIS simulation, report) and automatic failover across an
ordered chain when the primary fails (rate limits, 5xx, timeouts, bad
credentials).

Everything here is opt-in. Existing MiroFish setups — LLM_BASE_URL,
LLM_API_KEY, LLM_MODEL_NAME — keep working untouched: they form the
implicit single-entry "default" chain. The registry activates when a
config file or env var provides one.

Configuration methods, in priority order:

1. ``LLM_PROVIDERS_JSON`` — inline JSON env var (escape hatch for
   Docker-only setups; clunky, which is why it's last resort).
2. ``LLM_PROVIDERS_FILE`` — path to your own JSON file anywhere.
3. ``llm.config.json`` — the convention: a file auto-detected at
   ``backend/llm.config.json`` (dev) or ``/app/backend/llm.config.json``
   (container; the run script mounts it read-only when present).

Recommended: put ``llm.config.json`` next to your ``.env`` and let the
container pick it up — same workflow as today's LLM setup, no JSON in
env vars.

Config file schema::

    {
      "providers": [
        {
          "name": "cloudflare-gemma",
          "base_url": "https://api.cloudflare.com/client/v4/accounts/<id>/ai/v1",
          "api_key": "${CF_API_TOKEN}",          // env reference or literal
          "model": "@cf/google/gemma-4-26b-a4b-it",
          "enabled": true
        }
      ],
      "chains": {
        "default":    ["cloudflare-gemma", "openai-backup"],
        "ontology":   ["cloudflare-gemma"],
        "simulation": ["local-qwen"],
        "report":     ["openai-backup", "cloudflare-gemma"]
      }
    }

Stage names: ontology, profile, config, simulation, report, default.
A stage with no chain entry uses the "default" chain; with no registry
at all, every stage resolves to the legacy single provider.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

KNOWN_STAGES = ("ontology", "profile", "config", "simulation", "report", "default")

_ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")

# Environment variables the OASIS simulation subprocess reads at spawn.
SUBPROCESS_ENV_VARS = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL_NAME")


@dataclass(frozen=True)
class ProviderSpec:
    """One LLM provider as configured in the registry."""

    name: str
    base_url: str
    api_key: str
    model: str
    enabled: bool = True

    def to_public_dict(self) -> Dict[str, Any]:
        """Config suitable for API responses: never expose the API key."""
        return {
            "name": self.name,
            "base_url": self.base_url,
            "model": self.model,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class ChainEntry:
    """Concrete credentials for one attempt in a failover chain."""

    api_key: str
    base_url: str
    model: str
    provider: str  # registry provider name, or "legacy" for the env-var provider


class ProviderRegistry:
    """Validated provider registry with stage-based chain resolution."""

    def __init__(
        self,
        providers: Dict[str, ProviderSpec],
        chains: Dict[str, List[str]],
    ):
        self.providers = providers
        self.chains = chains

    def chain_for(self, stage: str) -> List[ChainEntry]:
        """Resolve the ordered chain for a stage.

        Falls back: stage chain -> "default" chain -> legacy provider.
        Disabled providers and unknown names are skipped (with a warning).
        """
        names = self.chains.get(stage) or self.chains.get("default") or []
        entries: List[ChainEntry] = []
        for name in names:
            spec = self.providers.get(name)
            if spec is None:
                logger.warning("LLM chain %r references unknown provider %r; skipping", stage, name)
                continue
            if not spec.enabled:
                logger.info("LLM provider %r is disabled; skipping in chain %r", name, stage)
                continue
            entries.append(
                ChainEntry(
                    api_key=spec.api_key,
                    base_url=spec.base_url,
                    model=spec.model,
                    provider=spec.name,
                )
            )
        return entries

    def enabled_providers(self) -> List[ProviderSpec]:
        return [p for p in self.providers.values() if p.enabled]


def _resolve_env_refs(value: Any) -> Any:
    """Resolve "${VAR}" references so secrets can stay in the environment."""
    if isinstance(value, str):
        match = _ENV_REF.match(value.strip())
        if match:
            resolved = os.environ.get(match.group(1), "")
            if not resolved:
                logger.warning("LLM provider env reference ${%s} is unset", match.group(1))
            return resolved
    return value


def _legacy_entry() -> ChainEntry:
    from ..config import Config

    return ChainEntry(
        api_key=Config.LLM_API_KEY or "",
        base_url=Config.LLM_BASE_URL,
        model=Config.LLM_MODEL_NAME,
        provider="legacy",
    )


def _parse_registry(raw: Dict[str, Any]) -> ProviderRegistry:
    providers: Dict[str, ProviderSpec] = {}
    for item in raw.get("providers", []):
        if not isinstance(item, dict) or not item.get("name"):
            logger.warning("LLM provider entry without a name; skipping: %r", item)
            continue
        name = str(item["name"])
        providers[name] = ProviderSpec(
            name=name,
            base_url=str(item.get("base_url", "")).strip(),
            api_key=str(_resolve_env_refs(item.get("api_key", ""))).strip(),
            model=str(item.get("model", "")).strip(),
            enabled=bool(item.get("enabled", True)),
        )

    chains: Dict[str, List[str]] = {}
    for stage, names in (raw.get("chains") or {}).items():
        stage_key = str(stage)
        if stage_key != "default" and stage_key not in KNOWN_STAGES:
            logger.warning("LLM chain for unknown stage %r; it will only apply if requested", stage_key)
        if isinstance(names, list):
            chains[stage_key] = [str(n) for n in names]

    return ProviderRegistry(providers=providers, chains=chains)


def _default_config_candidates() -> List[str]:
    """Well-known locations for llm.config.json, in priority order."""
    return [
        "/app/backend/llm.config.json",  # container mount (OpenReef run script)
        os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "llm.config.json")
        ),  # backend/llm.config.json (development)
    ]


def _load_raw() -> Optional[Dict[str, Any]]:
    inline = os.environ.get("LLM_PROVIDERS_JSON", "").strip()
    if inline:
        try:
            parsed = json.loads(inline)
        except json.JSONDecodeError as error:
            logger.error("LLM_PROVIDERS_JSON is not valid JSON: %s", error)
            return None
        return parsed if isinstance(parsed, dict) else None

    path = os.environ.get("LLM_PROVIDERS_FILE", "").strip()
    if not path:
        for candidate in _default_config_candidates():
            if os.path.exists(candidate):
                path = candidate
                break
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            parsed = json.load(handle)
    except OSError as error:
        logger.error("Cannot read LLM provider config %r: %s", path, error)
        return None
    if not isinstance(parsed, dict):
        logger.error("LLM provider config at %r is not a JSON object", path)
        return None
    return parsed


@lru_cache(maxsize=1)
def get_registry() -> Optional[ProviderRegistry]:
    """Load and cache the registry; None when unconfigured or invalid."""
    raw = _load_raw()
    if raw is None:
        return None
    registry = _parse_registry(raw)
    if not registry.providers:
        logger.error("LLM provider registry configured but contains no valid providers; ignoring")
        return None
    return registry


def clear_registry_cache() -> None:
    """Drop the cached registry (tests, hot reload)."""
    get_registry.cache_clear()


def resolve_chain(stage: str) -> List[ChainEntry]:
    """Ordered failover chain for a stage, ending at the legacy provider.

    Always returns at least one entry: when no registry matches the stage,
    this is exactly today's single-provider behavior.
    """
    registry = get_registry()
    if registry is not None:
        entries = registry.chain_for(stage)
        if entries:
            return entries
        logger.warning(
            "LLM registry has no usable providers for stage %r; falling back to legacy provider",
            stage,
        )
    return [_legacy_entry()]


def registry_active() -> bool:
    return get_registry() is not None


def subprocess_env_overlay(stage: str) -> Dict[str, str]:
    """Env overlay for spawned OASIS simulation subprocesses.

    The OASIS scripts read LLM_API_KEY/LLM_BASE_URL/LLM_MODEL_NAME from the
    environment at startup, so the chain head for the stage becomes the
    subprocess's provider. Empty when the legacy provider is in effect
    (subprocess inherits the same env as today).
    """
    head = chain_head_credentials(stage)
    if head is None:
        return {}
    return {
        "LLM_API_KEY": head[0],
        "LLM_BASE_URL": head[1],
        "LLM_MODEL_NAME": head[2],
    }


def chain_head_credentials(stage: str) -> Optional[tuple]:
    """(api_key, base_url, model) for the stage's chain head.

    Returns None when the legacy provider is in effect, so callers can keep
    their existing defaults untouched.
    """
    chain = resolve_chain(stage)
    head = chain[0] if chain else None
    if head is None or head.provider == "legacy":
        return None
    return (head.api_key, head.base_url, head.model)


def describe_configuration() -> Dict[str, Any]:
    """Registry state for the management API (no secrets)."""
    registry = get_registry()
    if registry is None:
        return {
            "active": False,
            "providers": [],
            "chains": {},
            "stages": list(KNOWN_STAGES),
        }
    return {
        "active": True,
        "providers": [p.to_public_dict() for p in registry.providers.values()],
        "chains": registry.chains,
        "stages": list(KNOWN_STAGES),
    }
