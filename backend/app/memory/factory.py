"""Memory backend factory (OpenReef ReefMemory).

``MEMORY_BACKEND`` chooses where graph memory lives:

- ``graphiti`` (default): native embedded backend — Graphiti on Kuzu, data
  under the uploads volume. No external service, no API key, works in the
  unchanged single-container image.
- ``zep``: legacy Zep Cloud (requires ZEP_API_KEY). Kept as an opt-in
  backend so existing setups keep working.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..config import Config

logger = logging.getLogger(__name__)

SUPPORTED_BACKENDS = ("graphiti", "zep", "auto")


def memory_backend_name() -> str:
    backend = (getattr(Config, "MEMORY_BACKEND", "graphiti") or "graphiti").lower()
    if backend == "auto":
        return "graphiti"
    if backend not in {"graphiti", "zep"}:
        raise ValueError(
            f"Unsupported MEMORY_BACKEND {backend!r}; expected one of graphiti|zep|auto"
        )
    return backend


_client = None


def get_memory_client(api_key: Optional[str] = None) -> Any:
    """Process-wide memory client, duck-typed like the zep_cloud client."""
    global _client
    if _client is not None:
        return _client

    backend = memory_backend_name()
    if backend == "zep":
        from ..utils.zep import get_zep_client

        logger.info("Memory backend: Zep Cloud (legacy opt-in)")
        return get_zep_client(api_key)

    from .graphiti_client import GraphitiCompatClient

    logger.info("Memory backend: ReefMemory (native Graphiti+Kuzu)")
    _client = GraphitiCompatClient()
    return _client


def reset_memory_client() -> None:
    """Drop the cached client (tests, config reload)."""
    global _client
    _client = None
