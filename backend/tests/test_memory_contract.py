"""Contract test: every memory-client call site must exist on the compat client.

This is the permanent net for the 'compat gap' bug class — call sites using
parts of the Zep surface the native backend hadn't implemented (batch
lifecycle, episode.processed, paging shapes). It walks all backend sources
for invocations and verifies the attribute chain exists on
GraphitiCompatClient. When a future change calls a new client method, this
test fails until the compat surface implements it.
"""

import re
from pathlib import Path

import pytest

from app.memory.graphiti_client import GraphitiCompatClient

BACKEND = Path(__file__).resolve().parents[1] / "app"

# (call-site regex, attribute chain on GraphitiCompatClient)
SURFACE = [
    (r"\.graph\.create\(", ["graph", "create"]),
    (r"\.graph\.get\(", ["graph", "get"]),
    (r"\.graph\.delete\(", ["graph", "delete"]),
    (r"\.graph\.set_ontology\(", ["graph", "set_ontology"]),
    (r"\.graph\.add\(", ["graph", "add"]),
    (r"\.graph\.search\(", ["graph", "search"]),
    (r"\.graph\.episode\.get\(", ["graph", "episode", "get"]),
    (r"\.graph\.node\.get\(", ["graph", "node", "get"]),
    (r"\.graph\.node\.get_edges\(", ["graph", "node", "get_edges"]),
    (r"\.graph\.node\.with_raw_response\.get_by_graph_id\(", ["graph", "node", "with_raw_response", "get_by_graph_id"]),
    (r"\.graph\.edge\.with_raw_response\.get_by_graph_id\(", ["graph", "edge", "with_raw_response", "get_by_graph_id"]),
    (r"\.batch\.create\(", ["batch", "create"]),
    (r"\.batch\.add\(", ["batch", "add"]),
    (r"\.batch\.process\(", ["batch", "process"]),
    (r"\.batch\.get\(", ["batch", "get"]),
    (r"\.batch\.list\(", ["batch", "list"]),
    (r"\.batch\.list_items\(", ["batch", "list_items"]),
]


def _iter_sources():
    for path in BACKEND.rglob("*.py"):
        if "memory" in path.parts or "openreef" in path.parts:
            continue  # the compat implementation itself
        yield path


def test_every_call_site_method_exists_on_compat_client():
    client = GraphitiCompatClient()
    missing = []
    for path in _iter_sources():
        source = path.read_text(encoding="utf-8")
        for pattern, chain in SURFACE:
            if re.search(pattern, source):
                obj = client
                for attr in chain:
                    obj = getattr(obj, attr, None)
                    if obj is None:
                        missing.append(f"{path.relative_to(BACKEND)}: .{'.'.join(chain)}")
                        break
    assert not missing, "Compat client missing call-site methods:\n" + "\n".join(missing)


def test_episode_get_exposes_processed_flag():
    from types import SimpleNamespace

    client = GraphitiCompatClient()
    # constructor is lazy; exercise the namespace directly with a fake driver
    episode_ns = client.graph.episode
    client._driver = SimpleNamespace()
    import app.memory.graphiti_client as module

    class _FakeEpisodic:
        @classmethod
        async def get_by_uuid(cls, driver, uuid):
            return SimpleNamespace(uuid=uuid, name="ep", content="x")

    real = module.EpisodicNode
    module.EpisodicNode = _FakeEpisodic
    try:
        episode = episode_ns.get("ep-1")
    finally:
        module.EpisodicNode = real
    assert episode.processed is True
    assert episode.status == "ready"
