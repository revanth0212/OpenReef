"""ReefMemory — OpenReef's native graph memory backend.

Public API:
    get_memory_client(api_key=None) -> client duck-typed like zep_cloud.Zep
    memory_backend_name() -> "graphiti" | "zep"
    reset_memory_client() -> drop the cached client (tests)

See docs/reef-memory.md for the full architecture notes.
"""

from .factory import get_memory_client, memory_backend_name, reset_memory_client

__all__ = ["get_memory_client", "memory_backend_name", "reset_memory_client"]
