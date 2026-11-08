"""Tests for ReefMemory: the native Graphiti+Kuzu memory backend.

The compat client is exercised with a fake Graphiti/driver so no LLM or
embedder is required — these tests verify the Zep-API mapping, the meta
store, lifecycle behavior, and backend selection.
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.config import Config
from app.memory import factory
from app.memory.factory import get_memory_client, memory_backend_name, reset_memory_client
from app.memory.graphiti_client import GraphitiCompatClient, _edge_to_ns, _node_to_ns


class _FakeEntityNode:
    _items: list = []
    deleted_groups: list = []

    @classmethod
    async def get_by_uuid(cls, driver, uuid):
        for item in cls._items:
            if item.uuid == uuid:
                return item
        return None

    @classmethod
    async def get_by_uuids(cls, driver, uuids):
        return [item for item in cls._items if item.uuid in set(uuids)]

    @classmethod
    async def get_by_group_ids(cls, driver, group_ids, **kwargs):
        return [item for item in cls._items if getattr(item, "group_id", None) in set(group_ids)]

    @classmethod
    async def delete_by_group_id(cls, driver, group_id, **kwargs):
        cls.deleted_groups.append(group_id)
        cls._items = [
            item for item in cls._items
            if getattr(item, "group_id", None) != group_id
        ]


class _FakeEpisodicNode(_FakeEntityNode):
    pass


class _FakeEntityEdge(_FakeEntityNode):
    @classmethod
    async def get_by_node_uuid(cls, driver, node_uuid):
        return [
            item for item in cls._items
            if getattr(item, "source_node_uuid", None) == node_uuid
            or getattr(item, "target_node_uuid", None) == node_uuid
        ]

    @classmethod
    async def delete_by_uuids(cls, driver, uuids):
        cls._items = [item for item in cls._items if item.uuid not in set(uuids)]


class _FakeSearch:
    def __init__(self, edges):
        self._edges = edges
        self.calls = []

    async def __call__(self, query, group_ids=None, num_results=10, search_filter=None):
        self.calls.append({"query": query, "group_ids": group_ids, "num_results": num_results})
        return self._edges[:num_results]


def _entity_node(uuid, name, group_id="g1", labels=None, attributes=None):
    return SimpleNamespace(
        uuid=uuid, name=name, group_id=group_id,
        labels=set(labels) if labels is not None else {"Person"},
        attributes=attributes or {},
        summary=f"summary of {name}", created_at=datetime.now(timezone.utc),
    )


def _entity_edge(uuid, fact, source, target, group_id="g1", name="RELATED_TO"):
    return SimpleNamespace(
        uuid=uuid, name=name, fact=fact, source_node_uuid=source,
        target_node_uuid=target, group_id=group_id, attributes={},
        created_at=datetime.now(timezone.utc),
        valid_at=None, invalid_at=None, expired_at=None,
    )


def make_client(monkeypatch, tmp_path, nodes=None, edges=None, episodes=None):
    import app.memory.graphiti_client as module

    client = GraphitiCompatClient()
    search = _FakeSearch(edges or [])
    fake_graphiti = SimpleNamespace(search=search)

    _FakeEntityNode._items = list(nodes or [])
    _FakeEntityEdge._items = list(edges or [])
    _FakeEpisodicNode._items = list(episodes or [])
    _FakeEntityNode.deleted_groups = []

    monkeypatch.setattr(module, "EntityNode", _FakeEntityNode)
    monkeypatch.setattr(module, "EntityEdge", _FakeEntityEdge)
    monkeypatch.setattr(module, "EpisodicNode", _FakeEpisodicNode)
    monkeypatch.setattr(client, "_ensure_graphiti", lambda: None)
    monkeypatch.setattr(client, "_driver", object())
    monkeypatch.setattr(client, "_graphiti", fake_graphiti)
    monkeypatch.setenv("REEF_MEMORY_DIR", str(tmp_path))
    return client, search


@pytest.fixture(autouse=True)
def reset_factory():
    reset_memory_client()
    yield
    reset_memory_client()


# ── backend selection ───────────────────────────────────────────────

def test_backend_defaults_to_graphiti(monkeypatch):
    monkeypatch.setattr(Config, "MEMORY_BACKEND", "graphiti")
    assert memory_backend_name() == "graphiti"


def test_backend_auto_resolves_to_graphiti(monkeypatch):
    monkeypatch.setattr(Config, "MEMORY_BACKEND", "auto")
    assert memory_backend_name() == "graphiti"


def test_backend_zep_uses_legacy_client(monkeypatch):
    monkeypatch.setattr(Config, "MEMORY_BACKEND", "zep")
    sentinel = object()
    # factory imports get_zep_client at call time from app.utils.zep
    monkeypatch.setattr("app.utils.zep.get_zep_client", lambda api_key=None: sentinel)
    assert get_memory_client() is sentinel


def test_backend_invalid_raises(monkeypatch):
    monkeypatch.setattr(Config, "MEMORY_BACKEND", "couchdb")
    with pytest.raises(ValueError, match="MEMORY_BACKEND"):
        memory_backend_name()


def test_graphiti_client_is_singleton(monkeypatch):
    monkeypatch.setattr(Config, "MEMORY_BACKEND", "graphiti")
    first = get_memory_client()
    assert isinstance(first, GraphitiCompatClient)
    assert get_memory_client() is first


# ── config validation ───────────────────────────────────────────────

def test_validate_graphiti_backend_does_not_require_zep_key(monkeypatch):
    monkeypatch.setattr(Config, "MEMORY_BACKEND", "graphiti")
    monkeypatch.setattr(Config, "ZEP_API_KEY", None)
    monkeypatch.setattr(Config, "LLM_API_KEY", "k")
    errors = Config.validate()
    assert not any("ZEP_API_KEY" in e for e in errors)


def test_validate_zep_backend_still_requires_key(monkeypatch):
    monkeypatch.setattr(Config, "MEMORY_BACKEND", "zep")
    monkeypatch.setattr(Config, "ZEP_API_KEY", None)
    monkeypatch.setattr(Config, "LLM_API_KEY", "k")
    errors = Config.validate()
    assert any("ZEP_API_KEY" in e for e in errors)


# ── serialization mapping ───────────────────────────────────────────

def test_node_mapping_exposes_zep_shape():
    node = _entity_node("n1", "Alice", labels=["PublicFigure"], attributes={"age": 30})
    ns = _node_to_ns(node)
    assert ns.uuid_ == "n1" and ns.uuid == "n1"
    assert ns.name == "Alice"
    assert ns.entity_type == "PublicFigure"
    assert ns.attributes == {"age": 30}
    assert "PublicFigure" in ns.labels


def test_node_mapping_defaults_entity_type():
    ns = _node_to_ns(_entity_node("n2", "Bob", labels=[], attributes={}))
    assert ns.entity_type == "Entity"


def test_edge_mapping_exposes_zep_shape():
    edge = _entity_edge("e1", "Alice knows Bob", "n1", "n2", name="KNOWS")
    ns = _edge_to_ns(edge)
    assert ns.uuid_ == "e1"
    assert ns.fact == "Alice knows Bob"
    assert ns.source_node_uuid == "n1"
    assert ns.target_node_uuid == "n2"
    assert ns.name == "KNOWS"


# ── graph lifecycle + reads ─────────────────────────────────────────

def test_graph_create_get_delete(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.graph.create(graph_id="g1", name="Graph One", description="d")
    info = client.graph.get("g1")
    assert info.status == "ready"

    with pytest.raises(Exception) as exc_info:  # zep_cloud NotFoundError
        client.graph.get("missing")
    assert getattr(exc_info.value, "status_code", None) == 404

    client.graph.delete("g1")
    assert "g1" in _FakeEntityNode.deleted_groups
    with pytest.raises(Exception):
        client.graph.get("g1")


def test_set_ontology_stored_in_meta(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.graph.create(graph_id="g1")
    client.graph.set_ontology(
        graph_ids=["g1"],
        entities={"Person": SimpleNamespace(model_dump=lambda: {"name": "Person"})},
        edges=None,
    )
    meta = json.loads((tmp_path / "graphs.json").read_text(encoding="utf-8"))
    assert meta["g1"]["ontology"]["entities"]["Person"]["name"] == "Person"


def test_set_ontology_accepts_pydantic_model_classes(monkeypatch, tmp_path):
    """Zep's external ontology API passes model CLASSES, not instances."""
    from pydantic import BaseModel

    class PersonEntity(BaseModel):
        name: str = "Person"
        description: str = "A person"

    class WorksFor(BaseModel):
        description: str = "employment"

    client, _ = make_client(monkeypatch, tmp_path)
    client.graph.create(graph_id="g1")
    client.graph.set_ontology(
        graph_ids=["g1"],
        entities={"PersonEntity": PersonEntity},
        edges={"WORKS_FOR": (WorksFor, [("PersonEntity", "OrganizationEntity")])},
    )
    meta = json.loads((tmp_path / "graphs.json").read_text(encoding="utf-8"))
    entities = meta["g1"]["ontology"]["entities"]
    schema = entities["PersonEntity"]
    assert schema["title"] == "PersonEntity"
    assert schema["properties"]["description"]["default"] == "A person"
    edge = meta["g1"]["ontology"]["edges"]["WORKS_FOR"]
    assert edge["model"]["title"] == "WorksFor"
    assert "PersonEntity" in edge["between"][0]


def test_set_ontology_never_crashes_on_odd_entries(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.graph.create(graph_id="g1")
    client.graph.set_ontology(
        graph_ids=["g1"],
        entities={"Weird": object(), "Broken": SimpleNamespace(model_dump=lambda: 1 / 0)},
        edges=None,
    )
    meta = json.loads((tmp_path / "graphs.json").read_text(encoding="utf-8"))
    assert "Weird" in meta["g1"]["ontology"]["entities"]


def test_node_get_and_edges(monkeypatch, tmp_path):
    nodes = [_entity_node("n1", "Alice"), _entity_node("n2", "Bob")]
    edges = [_entity_edge("e1", "f1", "n1", "n2")]
    client, _ = make_client(monkeypatch, tmp_path, nodes=nodes, edges=edges)

    node = client.graph.node.get("n1")
    assert node.name == "Alice"

    node_edges = client.graph.node.get_edges("n1")
    assert len(node_edges) == 1
    assert node_edges[0].fact == "f1"

    with pytest.raises(Exception) as exc_info:
        client.graph.node.get("nope")
    assert getattr(exc_info.value, "status_code", None) == 404


def test_raw_response_paging_single_page(monkeypatch, tmp_path):
    nodes = [_entity_node(str(i), f"N{i}") for i in range(3)]
    client, _ = make_client(monkeypatch, tmp_path, nodes=nodes)

    response = client.graph.node.with_raw_response.get_by_graph_id("g1", limit=100)
    assert len(response.data) == 3
    assert response.headers == {}  # no cursor → paging stops after page 1


def test_search_maps_edges_and_nodes(monkeypatch, tmp_path):
    nodes = [_entity_node("n1", "Alice"), _entity_node("n2", "Bob")]
    edges = [_entity_edge("e1", "Alice tweeted about Bob", "n1", "n2")]
    client, search = make_client(monkeypatch, tmp_path, nodes=nodes, edges=edges)

    results = client.graph.search("g1", query="tweet", limit=5)
    assert search.calls[0]["group_ids"] == ["g1"]
    assert len(results.edges) == 1
    assert results.edges[0].fact == "Alice tweeted about Bob"
    names = {node.name for node in results.nodes}
    assert names == {"Alice", "Bob"}


# ── episode writes ──────────────────────────────────────────────────

def test_graph_add_builds_episode(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    captured = {}

    def fake_add(graph_id, name, body, source_description, reference_time, metadata):
        captured.update(
            graph_id=graph_id, name=name, body=body,
            source_description=source_description,
            reference_time=reference_time, metadata=metadata,
        )
        return SimpleNamespace(episode=SimpleNamespace(uuid="ep-1"))

    monkeypatch.setattr(client, "_add_episode", fake_add)
    episode = client.graph.add(
        graph_id="g1",
        type="text",
        data="agent posted",
        created_at="2026-11-03T10:00:00Z",
        source_description="sim batch",
        metadata={"platform": "twitter"},
    )
    assert episode.uuid_ == "ep-1"
    assert captured["graph_id"] == "g1"
    assert captured["body"] == "agent posted"
    assert captured["reference_time"].tzinfo is not None
    assert captured["metadata"] == {"platform": "twitter"}


def test_batch_add_processes_items_sequentially(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    calls = []

    def fake_add(graph_id, name, body, source_description, reference_time, metadata):
        calls.append(body)
        return SimpleNamespace(episode=SimpleNamespace(uuid=f"ep-{len(calls)}"))

    monkeypatch.setattr(client, "_add_episode", fake_add)
    created = client.batch.create(metadata={"graph_id": "g1", "chunk_count": 3})
    items = [
        SimpleNamespace(graph_id="g1", data=f"chunk {i}", source_description="doc", metadata={"i": i})
        for i in range(3)
    ]
    details = client.batch.add(created.batch_id, items)
    assert calls == ["chunk 0", "chunk 1", "chunk 2"]
    assert [d.sequence_index for d in details] == [0, 1, 2]
    assert all(d.episode_uuid for d in details)


def test_batch_full_lifecycle(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        client, "_add_episode",
        lambda **kwargs: SimpleNamespace(episode=SimpleNamespace(uuid="ep-x")),
    )

    created = client.batch.create(metadata={"mirofish_operation_id": "op-1", "graph_id": "g1"})
    assert created.status == "draft" and created.batch_id

    summary = client.batch.get(created.batch_id)
    assert summary.status == "draft"
    assert summary.metadata["mirofish_operation_id"] == "op-1"

    client.batch.add(created.batch_id, [SimpleNamespace(graph_id="g1", data="x")])
    processed = client.batch.process(created.batch_id)
    assert processed.status == "succeeded"
    assert client.batch.get(created.batch_id).status == "succeeded"

    # reconciliation shapes used by graph_builder
    page = client.batch.list(limit=100)
    assert page.next_cursor is None
    assert any(b.batch_id == created.batch_id and b.metadata.get("mirofish_operation_id") == "op-1" for b in page.batches)
    page = client.batch.list_items(created.batch_id)
    assert page.next_cursor is None
    assert len(page.items) == 1 and page.items[0].episode_uuid == "ep-x"
    assert page.items[0].status == "succeeded"

    summary = client.batch.get(created.batch_id)
    assert summary.progress.percent_complete == 100.0
    assert summary.progress.succeeded_items == 1


def test_add_episode_never_passes_unsupported_kwargs(monkeypatch, tmp_path):
    """Mirror of graphiti-core 0.31's real add_episode signature: unknown
    kwargs must never be passed (the episode_metadata TypeError class)."""
    from graphiti_core import Graphiti
    import inspect

    real_params = set(inspect.signature(Graphiti.add_episode).parameters) - {"self"}

    client, _ = make_client(monkeypatch, tmp_path)
    calls = []

    strict = SimpleNamespace()

    async def strict_add_episode(**kwargs):
        calls.append(kwargs)
        unknown = set(kwargs) - real_params
        assert not unknown, f"unknown kwargs passed to add_episode: {unknown}"
        return SimpleNamespace(episode=SimpleNamespace(uuid="ep-s"))

    strict.add_episode = strict_add_episode
    monkeypatch.setattr(client, "_graphiti", strict)

    client._add_episode(
        graph_id="g1",
        name="ep",
        body="body",
        source_description="desc",
        reference_time=datetime.now(timezone.utc),
        metadata={"platform": "twitter", "round": 3},
    )
    sent = calls[0]
    assert "episode_metadata" not in sent
    assert "metadata=" in sent["source_description"]
    assert '"platform": "twitter"' in sent["source_description"]


def test_add_episode_omits_metadata_suffix_when_no_metadata(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    calls = []

    async def fake_add_episode(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(episode=SimpleNamespace(uuid="ep-n"))

    monkeypatch.setattr(client, "_graphiti", SimpleNamespace(add_episode=fake_add_episode))
    client._add_episode(
        graph_id="g1", name="ep", body="b", source_description="clean desc",
        reference_time=datetime.now(timezone.utc), metadata={},
    )
    assert calls[0]["source_description"] == "clean desc"


def test_batch_add_to_missing_batch_raises_404(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    with pytest.raises(Exception) as exc_info:
        client.batch.add("batch-nope", [])
    assert getattr(exc_info.value, "status_code", None) == 404


def test_episode_get_missing_raises_404(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    with pytest.raises(Exception) as exc_info:
        client.graph.episode.get("missing")
    assert getattr(exc_info.value, "status_code", None) == 404


# ── embedding provider independence ─────────────────────────────────

def test_embedder_credentials_default_to_chain_head(monkeypatch):
    from app.utils.llm_providers import ChainEntry

    monkeypatch.delenv("REEF_MEMORY_EMBEDDER_BASE_URL", raising=False)
    monkeypatch.delenv("REEF_MEMORY_EMBEDDER_API_KEY", raising=False)
    monkeypatch.delenv("REEF_MEMORY_EMBEDDER_MODEL", raising=False)
    monkeypatch.delenv("REEF_MEMORY_EMBEDDER_DIM", raising=False)
    client = GraphitiCompatClient()
    head = ChainEntry(api_key="pk", base_url="https://llm.example/v1", model="m", provider="p")
    creds = client._embedder_credentials(head)
    assert creds == {
        "base_url": "https://llm.example/v1",
        "api_key": "pk",
        "model": "text-embedding-3-small",
    }


def test_embedder_credentials_fully_overridable(monkeypatch):
    monkeypatch.setenv("REEF_MEMORY_EMBEDDER_BASE_URL", "https://embed.example/v1")
    monkeypatch.setenv("REEF_MEMORY_EMBEDDER_API_KEY", "embed-key")
    monkeypatch.setenv("REEF_MEMORY_EMBEDDER_MODEL", "custom-model")
    client = GraphitiCompatClient()
    head = SimpleNamespace(api_key="pk", base_url="https://llm.example/v1", model="m")
    creds = client._embedder_credentials(head)
    assert creds == {
        "base_url": "https://embed.example/v1",
        "api_key": "embed-key",
        "model": "custom-model",
    }


def test_embedding_dim_user_override_skips_probe(monkeypatch):
    monkeypatch.setenv("REEF_MEMORY_EMBEDDER_DIM", "1024")
    client = GraphitiCompatClient()

    def boom(*args, **kwargs):
        raise AssertionError("probe must not run when the override is set")

    monkeypatch.setattr(client, "_probe_embedding_dim", boom)
    assert client._resolve_embedding_dim({"base_url": "u", "api_key": "k", "model": "m"}) == 1024


def test_embedding_dim_probed_automatically(monkeypatch):
    monkeypatch.delenv("REEF_MEMORY_EMBEDDER_DIM", raising=False)
    client = GraphitiCompatClient()
    monkeypatch.setattr(client, "_probe_embedding_dim", lambda *a: 768)
    assert client._resolve_embedding_dim({"base_url": "u", "api_key": "k", "model": "m"}) == 768


def test_embedding_dim_probe_failure_points_to_env(monkeypatch):
    monkeypatch.delenv("REEF_MEMORY_EMBEDDER_DIM", raising=False)
    client = GraphitiCompatClient()

    def failing(*args, **kwargs):
        raise ConnectionError("no route")

    monkeypatch.setattr(client, "_probe_embedding_dim", failing)
    with pytest.raises(RuntimeError, match="REEF_MEMORY_EMBEDDER_DIM=768"):
        client._resolve_embedding_dim({"base_url": "u", "api_key": "k", "model": "m"})


def test_embedding_dim_invalid_override_rejected(monkeypatch):
    monkeypatch.setenv("REEF_MEMORY_EMBEDDER_DIM", "0")
    client = GraphitiCompatClient()
    with pytest.raises(ValueError, match="positive integer"):
        client._resolve_embedding_dim({"base_url": "u", "api_key": "k", "model": "m"})


# ── real embedded Kuzu (no mocks, no network) ───────────────────────

def test_real_kuzu_lifecycle_offline(monkeypatch, tmp_path):
    """Create/get/delete against a real embedded Kuzu database.

    Exercises schema init and group-scoped deletes without any LLM calls
    (no episodes are added). Proves the zero-infra native path works.
    """
    monkeypatch.setenv("REEF_MEMORY_DIR", str(tmp_path))
    monkeypatch.setenv("REEF_MEMORY_EMBEDDER_DIM", "768")  # skip the network probe
    client = GraphitiCompatClient()
    client._ensure_graphiti()

    client.graph.create(graph_id="g-real", name="Real")
    info = client.graph.get("g-real")
    assert info.status == "ready"
    assert info.node_count == 0 and info.edge_count == 0

    nodes_page = client.graph.node.with_raw_response.get_by_graph_id("g-real")
    assert nodes_page.data == []

    client.graph.set_ontology(graph_ids=["g-real"], entities={"Person": None}, edges=None)
    meta = json.loads((tmp_path / "graphs.json").read_text(encoding="utf-8"))
    assert "g-real" in meta

    client.graph.delete("g-real")
    with pytest.raises(Exception):
        client.graph.get("g-real")


# ── regression: API layer must not gate on ZEP_API_KEY unconditionally ──

@pytest.mark.parametrize("path", ["app/api/graph.py", "app/api/simulation.py"])
def test_api_layer_has_no_unconditional_zep_gates(path):
    from pathlib import Path

    source = Path(path).read_text(encoding="utf-8")
    for lineno, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if "if not Config.ZEP_API_KEY:" in stripped:
            pytest.fail(f"{path}:{lineno} unconditional ZEP_API_KEY gate")
        if "if not Config.ZEP_API_KEY" in stripped and "memory_backend_name()" not in stripped:
            pytest.fail(f"{path}:{lineno} ZEP gate missing backend condition")


def test_kuzu_driver_patched_for_graphiti_scope_resolution():
    """graphiti 0.31's _resolve_request_scope must find _database and a
    working clone() on the (deprecated) KuzuDriver."""
    from graphiti_core.driver.kuzu_driver import KuzuDriver

    from app.memory.graphiti_client import _patch_kuzu_driver_for_graphiti

    driver = _patch_kuzu_driver_for_graphiti(KuzuDriver(":memory:"))
    assert driver._database is None
    assert driver.clone(database="mirofish_abc123") is driver

    # the real scope-resolution check, exercised exactly as graphiti does it
    group_id = "mirofish_abc123"
    if group_id == driver._database:
        scoped = driver
    else:
        scoped = driver.clone(database=group_id)
    assert scoped is driver


def test_all_operations_share_one_event_loop(monkeypatch, tmp_path):
    """The asyncio.run-per-call pattern died with 'Event loop is closed'
    once pooled HTTP connections were reused across calls (real-world:
    25-chunk doc). All operations must run on one dedicated loop."""
    import asyncio as _asyncio

    client, _ = make_client(monkeypatch, tmp_path)
    loops = []

    async def fake_add_episode(**kwargs):
        loops.append(_asyncio.get_running_loop())
        await _asyncio.sleep(0)  # yield, letting other tasks interleave
        return SimpleNamespace(episode=SimpleNamespace(uuid="ep-loop"))

    monkeypatch.setattr(client, "_graphiti", SimpleNamespace(add_episode=fake_add_episode))

    for i in range(8):  # more calls than the failing real-world batch
        client._add_episode(
            graph_id="g1", name=f"ep-{i}", body="b", source_description="d",
            reference_time=datetime.now(timezone.utc), metadata={},
        )
    assert len(loops) == 8
    assert len(set(loops)) == 1, "operations ran on multiple event loops"
    assert client._loop is not None
    assert all(l is client._loop for l in loops)
