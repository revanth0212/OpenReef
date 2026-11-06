"""Local-model adapter: detection + request adaptation (OpenReef plugin).

Small local OpenAI-compatible servers (oMLX, llama.cpp, LM Studio, …)
behave differently from hosted APIs in three ways that matter to
MiroFish's structured-JSON workload:

1. ``json_object`` response_format is often mapped to "any JSON value",
   so small models wrap answers in an ARRAY — MiroFish requires a root
   object and rejects the response. The proven fix (previously shipped as
   an external proxy "shim") is to request a JSON *schema* constrained to
   a root object instead.
2. Without a token cap, a rambling model generates to the server's limit
   (~32k) and fails after minutes instead of seconds.
3. A mild repetition penalty breaks repetition loops small models fall
   into on long structured outputs.

This adapter applies those transforms in-process at the single chat-
completions choke point — no proxy container, no extra port. It
auto-activates whenever the configured endpoint looks local, per provider
(so mixed local/remote failover chains adapt per provider), and is a
no-op for hosted endpoints.

Override with ``OPENREEF_LOCAL_LLM=on|off`` (default ``auto``).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

MODE_ENV = "OPENREEF_LOCAL_LLM"
MAX_TOKENS_CAP = 4096
REP_PENALTY = 1.05

ROOT_OBJECT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "mirofish_root_object",
        "schema": {"type": "object"},
    },
}

# Hosts/namespaces that mean "served by the machine running (or hosting)
# this app" — the endpoints local-model servers listen on.
_LOCAL_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "host.container.internal",   # Apple containers → host
    "host.docker.internal",      # Docker Desktop → host
}
_LOCAL_SUFFIXES = (".local", ".internal", ".lan", ".home.arpa")

_announced = False


def local_llm_mode(base_url: Optional[str] = None) -> bool:
    """Should local-model adaptations apply for this endpoint?

    Explicit ``OPENREEF_LOCAL_LLM=on|off`` wins. In ``auto`` (default) we
    treat loopback host addresses and RFC-2606-style local names as local.
    """
    setting = os.environ.get(MODE_ENV, "auto").strip().lower()
    if setting in ("on", "1", "true", "yes"):
        return True
    if setting in ("off", "0", "false", "no"):
        return False

    if not base_url:
        return False
    host = (urlparse(base_url).hostname or "").lower()
    if not host:
        return False
    if host in _LOCAL_HOSTS:
        return True
    return any(host.endswith(suffix) for suffix in _LOCAL_SUFFIXES)


def apply_local_model_adaptations(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the local-server transforms to chat-completion kwargs.

    Mirrors the behavior of the proven external shim: root-object JSON
    schema instead of json_object, max-tokens cap, repetition penalty.
    """
    adapted = dict(kwargs)

    response_format = adapted.get("response_format")
    if isinstance(response_format, dict) and response_format.get("type") == "json_object":
        adapted["response_format"] = ROOT_OBJECT_RESPONSE_FORMAT

    for key in ("max_tokens", "max_completion_tokens"):
        value = adapted.get(key)
        if value is None or int(value) > MAX_TOKENS_CAP:
            adapted[key] = MAX_TOKENS_CAP

    if REP_PENALTY:
        adapted.setdefault("repetition_penalty", REP_PENALTY)

    return adapted


def maybe_adapt_for_client(client: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Hook used by the chat-compat layer: adapt when the client's endpoint
    is local, pass through otherwise. Logs activation once per process."""
    global _announced
    base_url = str(getattr(client, "base_url", "") or "")
    if not local_llm_mode(base_url):
        return kwargs
    if not _announced:
        _announced = True
        logger.info(
            "OpenReef local-model mode: request adaptation active for %s "
            "(root-object JSON schema, max_tokens<=%s, repetition_penalty=%s; "
            "disable with %s=off)",
            base_url,
            MAX_TOKENS_CAP,
            REP_PENALTY,
            MODE_ENV,
        )
    return apply_local_model_adaptations(kwargs)


def reset_announcement_flag() -> None:
    """Test hook."""
    global _announced
    _announced = False
