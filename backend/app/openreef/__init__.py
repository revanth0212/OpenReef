"""OpenReef adapter layer.

Everything in this package is additive and lives OUTSIDE upstream MiroFish
code paths: adapters auto-activate from configuration and are no-ops when
unused, so merging upstream MiroFish updates stays trivial.

Adapters:
- localmodels: local-model detection + in-process request adaptation
  (replaces the old external LLM shim — no proxy, no extra port).
"""

from .localmodels import apply_local_model_adaptations, local_llm_mode

__all__ = ["apply_local_model_adaptations", "local_llm_mode"]
