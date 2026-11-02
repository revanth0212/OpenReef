"""LLM provider management endpoints (OpenReef multi-LLM support).

Read-only view of the registry plus a connectivity test. Providers are
configured via LLM_PROVIDERS_JSON / LLM_PROVIDERS_FILE (see
app/utils/llm_providers.py); API keys are never returned by these
endpoints.
"""

import time
import traceback

from flask import jsonify, request

from . import llm_bp
from ..utils.llm_providers import describe_configuration
from ..utils.logger import get_logger
from ..utils.openai_chat_compat import (
    create_chat_completion,
    extract_chat_completion_text,
)
from openai import OpenAI

logger = get_logger("mirofish.api.llm")


@llm_bp.route('/providers', methods=['GET'])
def list_providers():
    """Registry state (no secrets) plus the active legacy model for context."""
    from ..config import Config

    info = describe_configuration()
    info["legacy"] = {
        "base_url": Config.LLM_BASE_URL,
        "model": Config.LLM_MODEL_NAME,
        "configured": bool(Config.LLM_API_KEY),
    }
    return jsonify({"success": True, "data": info})


@llm_bp.route('/providers/test', methods=['POST'])
def test_provider():
    """Ping a provider with a tiny chat completion to verify connectivity.

    Body: {"base_url": ..., "api_key": ..., "model": ...}
    Response: {"success": true, "data": {"ok": bool, "latency_ms": float, "error": str|None}}
    """
    body = request.get_json(silent=True) or {}
    base_url = str(body.get("base_url", "")).strip()
    api_key = str(body.get("api_key", "")).strip()
    model = str(body.get("model", "")).strip()

    missing = [
        name
        for name, value in (("base_url", base_url), ("api_key", api_key), ("model", model))
        if not value
    ]
    if missing:
        return jsonify({
            "success": False,
            "error": f"missing required fields: {', '.join(missing)}",
        }), 400

    started = time.monotonic()
    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=30.0)
        response = create_chat_completion(
            client,
            model=model,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            temperature=0.0,
            max_tokens=8,
            response_format=None,
        )
        text = extract_chat_completion_text(response)
        latency_ms = round((time.monotonic() - started) * 1000, 1)
        return jsonify({
            "success": True,
            "data": {
                "ok": bool(text.strip()),
                "latency_ms": latency_ms,
                "response_preview": text.strip()[:120],
                "error": None,
            },
        })
    except Exception as error:
        latency_ms = round((time.monotonic() - started) * 1000, 1)
        logger.warning("LLM provider test failed: %s", error)
        return jsonify({
            "success": True,
            "data": {
                "ok": False,
                "latency_ms": latency_ms,
                "response_preview": "",
                "error": f"{type(error).__name__}: {error}",
            },
        })
