"""ReefMemory: native memory backend for OpenReef.

Replaces the Zep Cloud dependency with memory that lives with the tool:
an embedded graph database (Kuzu — zero services, data under the existing
uploads volume) driven by Graphiti (getzep/graphiti, Apache-2.0 — Zep's
own open-source engine: temporal knowledge graph, entity/edge extraction,
hybrid semantic search).

This module exposes a *Zep-SDK-compatible duck-typed client* so the rest
of MiroFish keeps working unchanged — graph_builder, zep_tools, the
simulation updater, and the graph API all speak the same calls they used
against Zep Cloud.

Known v1 trade-offs (documented in docs/reef-memory.md):
- Ontology types are stored but not yet enforced during extraction.
- batch.add processes items sequentially (correctness over throughput).
- The Kuzu graphiti driver is deprecated upstream; migrating to FalkorDB
  later is a driver swap, not a redesign.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import httpx
from zep_cloud import NotFoundError

from graphiti_core import errors as graphiti_errors

from ..config import Config
from ..utils.llm_providers import resolve_chain

# Imported at module level so tests can monkeypatch the names; graphiti is
# an installed dependency of the fork.
from graphiti_core.nodes import EntityNode, EpisodicNode
from graphiti_core.edges import EntityEdge

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDER_MODEL = "text-embedding-3-small"


def _not_found(message: str) -> NotFoundError:
    return NotFoundError(body={"message": message})


def _safe_ontology_schema(item: Any) -> Any:
    """Best-effort JSON snapshot of an ontology entry (stored, not enforced).

    Zep's external ontology API takes pydantic model CLASSES, so entries
    may be classes, instances, or (for edges) tuples of
    (model_class, source_target_pairs). Never crash on any of them.
    """
    try:
        if isinstance(item, tuple) and item:
            return {
                "model": _safe_ontology_schema(item[0]),
                "between": [str(part) for part in item[1:]],
            }
        if isinstance(item, type):  # pydantic model class
            if hasattr(item, "model_json_schema"):
                return item.model_json_schema()
            if hasattr(item, "schema"):
                return item.schema()
            return str(item)
        if hasattr(item, "model_dump"):
            return item.model_dump()
        if hasattr(item, "dict"):
            return item.dict()
        return str(item)
    except Exception:
        return str(item)


def _parse_rfc3339(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _edge_to_ns(edge: Any) -> Any:
    return SimpleNamespace(
        uuid_=edge.uuid,
        uuid=edge.uuid,
        name=getattr(edge, "name", "") or "",
        fact=getattr(edge, "fact", "") or "",
        source_node_uuid=getattr(edge, "source_node_uuid", "") or "",
        target_node_uuid=getattr(edge, "target_node_uuid", "") or "",
        attributes=dict(getattr(edge, "attributes", None) or {}),
        created_at=getattr(edge, "created_at", None),
        valid_at=getattr(edge, "valid_at", None),
        invalid_at=getattr(edge, "invalid_at", None),
        expired_at=getattr(edge, "expired_at", None),
    )


def _node_to_ns(node: Any) -> Any:
    labels = sorted(getattr(node, "labels", None) or [])
    attributes = dict(getattr(node, "attributes", None) or {})
    entity_type = attributes.get("entity_type") or (labels[0] if labels else "Entity")
    return SimpleNamespace(
        uuid_=node.uuid,
        uuid=node.uuid,
        name=getattr(node, "name", "") or "",
        entity_type=entity_type,
        labels=labels,
        attributes=attributes,
        summary=getattr(node, "summary", "") or "",
        created_at=getattr(node, "created_at", None),
        group_id=getattr(node, "group_id", None),
    )


class _RawResponse:
    """Mimics the SDK's with_raw_response shape: .data list + .headers dict."""

    def __init__(self, data: List[Any]):
        self.data = data
        self.headers = {}


class _EpisodeNamespace:
    def __init__(self, owner: "GraphitiCompatClient"):
        self._owner = owner

    def get(self, uuid_: str) -> Any:
        episode = self._owner._run_optional(
            EpisodicNode.get_by_uuid(driver=self._owner._driver, uuid=uuid_)
        )
        if episode is None:
            raise _not_found(f"episode {uuid_} not found")
        return SimpleNamespace(
            uuid_=episode.uuid,
            uuid=episode.uuid,
            name=getattr(episode, "name", "") or "",
            content=getattr(episode, "content", "") or "",
            status="ready",  # synchronous backend: ingestion is complete
        )


class _NodeNamespace:
    def __init__(self, owner: "GraphitiCompatClient"):
        self._owner = owner
        self.with_raw_response = self

    def get(self, uuid_: str) -> Any:
        node = self._owner._run_optional(
            EntityNode.get_by_uuid(driver=self._owner._driver, uuid=uuid_)
        )
        if node is None:
            raise _not_found(f"node {uuid_} not found")
        return _node_to_ns(node)

    def get_edges(self, node_uuid: str) -> List[Any]:
        edges = self._owner._run_list(
            EntityEdge.get_by_node_uuid(driver=self._owner._driver, node_uuid=node_uuid)
        )
        return [_edge_to_ns(edge) for edge in edges]

    def get_by_graph_id(self, graph_id: str, limit: int = 100, cursor: Any = None) -> Any:
        nodes = self._owner._run_list(
            EntityNode.get_by_group_ids(driver=self._owner._driver, group_ids=[graph_id])
        )
        return _RawResponse([_node_to_ns(node) for node in nodes])


class _EdgeNamespace:
    def __init__(self, owner: "GraphitiCompatClient"):
        self._owner = owner
        self.with_raw_response = self

    def get_by_graph_id(self, graph_id: str, limit: int = 100, cursor: Any = None) -> Any:
        edges = self._owner._run_list(
            EntityEdge.get_by_group_ids(driver=self._owner._driver, group_ids=[graph_id])
        )
        return _RawResponse([_edge_to_ns(edge) for edge in edges])


class _BatchNamespace:
    def __init__(self, owner: "GraphitiCompatClient"):
        self._owner = owner

    def add(self, batch_id: str, items: List[Any]) -> List[Any]:
        """Process BatchAddItem entries (graph_episode type) sequentially."""
        details = []
        for index, item in enumerate(items):
            graph_id = getattr(item, "graph_id", None)
            data = getattr(item, "data", "")
            source_description = getattr(item, "source_description", "MiroFish batch item")
            metadata = getattr(item, "metadata", None) or {}
            results = self._owner._add_episode(
                graph_id=graph_id,
                name=f"{batch_id}-{index}",
                body=str(data),
                source_description=source_description,
                reference_time=datetime.now(timezone.utc),
                metadata=metadata,
            )
            details.append(
                SimpleNamespace(
                    sequence_index=index,
                    uuid=getattr(results.episode, "uuid", None) if results else None,
                    status="ok",
                )
            )
        return details


class _GraphNamespace:
    def __init__(self, owner: "GraphitiCompatClient"):
        self._owner = owner
        self.episode = _EpisodeNamespace(owner)
        self.node = _NodeNamespace(owner)
        self.edge = _EdgeNamespace(owner)

    def create(self, graph_id: str, name: str = "", description: str = "") -> Any:
        meta = self._owner._load_meta()
        if graph_id in meta:
            raise _not_found(f"graph {graph_id} already exists")
        meta[graph_id] = {
            "name": name or graph_id,
            "description": description,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "ontology": None,
        }
        self._owner._save_meta(meta)
        return SimpleNamespace(name=graph_id, status="ready")

    def get(self, graph_id: str) -> Any:
        meta = self._owner._load_meta()
        if graph_id not in meta:
            raise _not_found(f"graph {graph_id} not found")
        nodes = self._owner._run_list(
            EntityNode.get_by_group_ids(driver=self._owner._driver, group_ids=[graph_id])
        )
        edges = self._owner._run_list(
            EntityEdge.get_by_group_ids(driver=self._owner._driver, group_ids=[graph_id])
        )
        return SimpleNamespace(
            name=graph_id,
            status="ready",
            node_count=len(nodes),
            edge_count=len(edges),
        )

    def delete(self, graph_id: str) -> Any:
        self._owner._delete_graph_data(graph_id)
        meta = self._owner._load_meta()
        meta.pop(graph_id, None)
        self._owner._save_meta(meta)
        return SimpleNamespace(name=graph_id, status="deleted")

    def set_ontology(
        self,
        graph_ids: List[str],
        entities: Optional[Dict[str, Any]] = None,
        edges: Optional[Dict[str, Any]] = None,
    ) -> Any:
        meta = self._owner._load_meta()
        for graph_id in graph_ids or []:
            entry = meta.setdefault(graph_id, {"name": graph_id, "created_at": datetime.now(timezone.utc).isoformat()})
            entry["ontology"] = {
                "entities": {name: _safe_ontology_schema(model) for name, model in (entities or {}).items()},
                "edges": {name: _safe_ontology_schema(model) for name, model in (edges or {}).items()},
            }
        self._owner._save_meta(meta)
        return SimpleNamespace(status="ok")

    def add(
        self,
        graph_id: str,
        type: str = "text",
        data: str = "",
        created_at: Any = None,
        source_description: str = "MiroFish episode",
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        results = self._owner._add_episode(
            graph_id=graph_id,
            name=kwargs.get("name") or f"episode-{datetime.now(timezone.utc).timestamp()}",
            body=data,
            source_description=source_description,
            reference_time=_parse_rfc3339(created_at),
            metadata=metadata or {},
        )
        episode = results.episode if results else None
        return SimpleNamespace(
            uuid_=getattr(episode, "uuid", None),
            uuid=getattr(episode, "uuid", None),
            status="ready",
        )

    def search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        scope: str = "edges",
        reranker: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        from graphiti_core.search.search_filters import SearchFilters

        filters = SearchFilters(group_ids=[graph_id])
        edges = self._owner._run(
            self._owner._graphiti.search(
                query=query,
                group_ids=[graph_id],
                num_results=limit,
                search_filter=filters,
            )
        )
        edge_list = [_edge_to_ns(edge) for edge in edges or []]

        node_uuids: List[str] = []
        for edge in edge_list:
            for uuid in (edge.source_node_uuid, edge.target_node_uuid):
                if uuid and uuid not in node_uuids:
                    node_uuids.append(uuid)
        nodes = []
        if node_uuids:
            fetched = self._owner._run_list(
                EntityNode.get_by_uuids(driver=self._owner._driver, uuids=node_uuids)
            )
            nodes = [_node_to_ns(node) for node in fetched]

        return SimpleNamespace(edges=edge_list, nodes=nodes)


class GraphitiCompatClient:
    """Duck-typed replacement for the zep_cloud client used by MiroFish."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._graphiti = None
        self._driver = None
        self._meta_lock = threading.Lock()
        self.graph = _GraphNamespace(self)
        self.batch = _BatchNamespace(self)

    # ── internal plumbing ────────────────────────────────────────────

    @property
    def _db_dir(self) -> str:
        path = os.environ.get("REEF_MEMORY_DIR") or os.path.join(
            Config.UPLOAD_FOLDER, "reef-memory"
        )
        os.makedirs(path, exist_ok=True)
        return path

    @property
    def _meta_path(self) -> str:
        return os.path.join(self._db_dir, "graphs.json")

    def _load_meta(self) -> Dict[str, Any]:
        with self._meta_lock:
            try:
                with open(self._meta_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                    return data if isinstance(data, dict) else {}
            except (OSError, json.JSONDecodeError):
                return {}

    def _save_meta(self, meta: Dict[str, Any]) -> None:
        with self._meta_lock:
            tmp_path = self._meta_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(meta, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._meta_path)

    def _run(self, coro: Any) -> Any:
        """Run a graphiti coroutine to completion, serialized (Kuzu is 1-writer)."""
        with self._lock:
            return asyncio.run(coro)

    def _run_optional(self, coro: Any) -> Any:
        """Like _run, but graphiti's NotFound errors become None."""
        try:
            return self._run(coro)
        except (
            graphiti_errors.NodeNotFoundError,
            graphiti_errors.EdgeNotFoundError,
            graphiti_errors.GroupsNodesNotFoundError,
            graphiti_errors.GroupsEdgesNotFoundError,
        ):
            return None

    def _run_list(self, coro: Any) -> List[Any]:
        """Like _run, but graphiti's empty-group errors become []."""
        result = self._run_optional(coro)
        return list(result or [])

    def _embedder_credentials(self, head: Any) -> Dict[str, Any]:
        """Embedding endpoint credentials.

        Defaults to the ontology chain head (same provider as extraction),
        with independent overrides so embeddings can live on a different
        provider entirely:

            REEF_MEMORY_EMBEDDER_MODEL      embedding model name
            REEF_MEMORY_EMBEDDER_BASE_URL   provider base URL (optional)
            REEF_MEMORY_EMBEDDER_API_KEY    provider key (optional)
        """
        return {
            "base_url": os.environ.get("REEF_MEMORY_EMBEDDER_BASE_URL") or head.base_url or None,
            "api_key": os.environ.get("REEF_MEMORY_EMBEDDER_API_KEY") or head.api_key or "missing",
            "model": os.environ.get("REEF_MEMORY_EMBEDDER_MODEL", DEFAULT_EMBEDDER_MODEL),
        }

    def _probe_embedding_dim(self, base_url: str, api_key: str, model: str) -> int:
        """Ask the embeddings endpoint for its vector dimension (one tiny call).

        Runs at memory-backend init — models change daily, so we measure
        instead of maintaining a lookup table.
        """
        url = (base_url or "https://api.openai.com/v1").rstrip("/") + "/embeddings"
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "input": "dimension probe"},
            )
            response.raise_for_status()
            payload = response.json()
        vectors = payload.get("data") or []
        if not vectors or not vectors[0].get("embedding"):
            raise RuntimeError(f"embeddings endpoint returned no vector for model {model!r}")
        return len(vectors[0]["embedding"])

    def _resolve_embedding_dim(self, creds: Dict[str, Any]) -> int:
        """User override wins; otherwise probe the endpoint automatically."""
        override = os.environ.get("REEF_MEMORY_EMBEDDER_DIM")
        if override:
            dim = int(override)
            if dim < 1:
                raise ValueError("REEF_MEMORY_EMBEDDER_DIM must be a positive integer")
            return dim
        try:
            return self._probe_embedding_dim(
                creds["base_url"] or "", creds["api_key"] or "", creds["model"]
            )
        except Exception as error:
            raise RuntimeError(
                "ReefMemory cannot determine the embedding vector dimension "
                f"automatically ({error}). Set it explicitly in .env, e.g. "
                "REEF_MEMORY_EMBEDDER_DIM=768"
            ) from error

    def _ensure_graphiti(self) -> None:
        if self._graphiti is not None:
            return
        from graphiti_core import Graphiti
        from graphiti_core.cross_encoder.client import CrossEncoderClient
        from graphiti_core.driver.kuzu_driver import KuzuDriver
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
        from graphiti_core.llm_client.config import LLMConfig
        from graphiti_core.llm_client.openai_client import OpenAIClient

        class _IdentityCrossEncoder(CrossEncoderClient):
            """Provider-neutral reranker: keep candidate order, neutral scores.

            Graphiti requires a cross encoder at construction; the OpenAI
            reranker default breaks providers without a rerank endpoint, so
            the native backend ships an identity reranker instead.
            """

            async def rank(self, query: str, passages: list) -> list:
                return [(passage, 0.5) for passage in passages]

        chain = resolve_chain("ontology")
        head = chain[0]
        embedder_creds = self._embedder_credentials(head)

        driver = KuzuDriver(db=os.path.join(self._db_dir, "kuzu.db"))
        llm_client = OpenAIClient(
            LLMConfig(
                api_key=head.api_key or "missing",
                model=head.model,
                base_url=head.base_url or None,
                small_model=head.model,
            )
        )
        embedder = OpenAIEmbedder(
            OpenAIEmbedderConfig(
                api_key=embedder_creds["api_key"],
                base_url=embedder_creds["base_url"],
                embedding_model=embedder_creds["model"],
                embedding_dim=self._resolve_embedding_dim(embedder_creds),
            )
        )
        logger.info(
            "ReefMemory: embedded graph store at %s (extract=%s, embed=%s @ %s)",
            self._db_dir,
            head.model,
            embedder_creds["model"],
            embedder_creds["base_url"] or "same-as-extraction",
        )
        self._driver = driver
        self._graphiti = Graphiti(
            graph_driver=driver,
            llm_client=llm_client,
            embedder=embedder,
            cross_encoder=_IdentityCrossEncoder(),
            store_raw_episode_content=True,
        )

    def _delete_graph_data(self, graph_id: str) -> None:
        edges = self._run_list(
            EntityEdge.get_by_group_ids(driver=self._driver, group_ids=[graph_id])
        )
        uuids = [edge.uuid for edge in edges or []]
        if uuids:
            self._run(EntityEdge.delete_by_uuids(driver=self._driver, uuids=uuids))
        self._run(EntityNode.delete_by_group_id(driver=self._driver, group_id=graph_id))
        self._run(EpisodicNode.delete_by_group_id(driver=self._driver, group_id=graph_id))

    def _add_episode(
        self,
        graph_id: str,
        name: str,
        body: str,
        source_description: str,
        reference_time: datetime,
        metadata: Dict[str, Any],
    ) -> Any:
        from graphiti_core.nodes import EpisodeType

        self._ensure_graphiti()
        meta_payload = [json.dumps(metadata, ensure_ascii=False)] if metadata else []
        return self._run(
            self._graphiti.add_episode(
                name=name,
                episode_body=body,
                source=EpisodeType.message,
                source_description=source_description,
                reference_time=reference_time,
                group_id=graph_id,
                episode_metadata=meta_payload,
            )
        )
