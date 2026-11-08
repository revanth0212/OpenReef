#!/usr/bin/env python3
"""End-to-end ReefMemory verification against REAL components (no mocks).

Drives the exact service layer the app uses — GraphBuilderService
(create_graph, set_ontology, add_text_batches with batch lifecycle),
entity reads, ZepToolsService search (exercises embeddings), and
deletion — on the native Graphiti+Kuzu backend with the configured
providers (real extraction + embedding calls; cost is a few cents).

Run from backend/:
    uv run python scripts/verify_reef_memory.py

Exits 0 and prints ALL CHECKS PASSED on success; exits 1 with the
failing step + traceback otherwise. This is the pre-flight gate before
asking anyone to test in a container.
"""

import os
import sys
import tempfile
import time
import traceback

_TMPDIR = tempfile.mkdtemp(prefix="reef-verify-")

from app.config import Config  # noqa: E402  (loads .env at import)

# Post-import env (dotenv override would clobber pre-import values).
os.environ["MEMORY_BACKEND"] = "graphiti"
os.environ["REEF_MEMORY_DIR"] = _TMPDIR
_embed_url = os.environ.get("REEF_MEMORY_EMBEDDER_BASE_URL", "")
if "host.container.internal" in _embed_url:
    # Containers' host DNS name doesn't resolve on the host; oMLX is local.
    os.environ["REEF_MEMORY_EMBEDDER_BASE_URL"] = _embed_url.replace(
        "host.container.internal", "localhost"
    )

FAILED = False


def step(label):
    print(f"\n=== {label} ===", flush=True)


def check(label, condition, detail=""):
    global FAILED
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label} {detail}", flush=True)
    if not condition:
        FAILED = True


def main():
    step("backend selection")
    from app.memory import memory_backend_name
    check("memory backend is graphiti", memory_backend_name() == "graphiti")

    from app.services.graph_builder import GraphBuilderService
    from app.services.zep_entity_reader import ZepEntityReader
    from app.services.zep_tools import ZepToolsService

    step("service construction without ZEP_API_KEY")
    service = GraphBuilderService(api_key=None)  # must not raise
    check("GraphBuilderService(api_key=None) constructed", service is not None)

    step("graph lifecycle: create")
    graph_id = service.create_graph(name="reef-verify")
    check("graph created", graph_id.startswith("mirofish_"), graph_id)
    info = service.client.graph.get(graph_id)
    check("graph.get reports ready", info.status == "ready")

    step("set_ontology (dynamic pydantic classes + tuples)")
    ontology = {
        "entity_types": [
            {
                "name": "PersonEntity",
                "description": "A person involved in the scenario",
                "attributes": [{"name": "role", "description": "their role"}],
            },
            {
                "name": "OrganizationEntity",
                "description": "An organization",
                "attributes": [],
            },
        ],
        "edge_types": [
            {
                "name": "WORKS_FOR",
                "description": "employment relationship",
                "source_targets": [
                    {"source": "PersonEntity", "target": "OrganizationEntity"}
                ],
            },
        ],
    }
    service.set_ontology(graph_id, ontology)
    meta_check = service.client.graph.get(graph_id)
    check("set_ontology accepted", meta_check.status == "ready")

    step("ingestion: batch create -> add -> process -> episode poll")
    chunks = [
        "Alice, a battery researcher, works at Acme Labs. "
        "Alice published a widely discussed paper on solid-state batteries.",
        "Acme Labs is a research organization founded in 2019. "
        "It partners with Northfield University on energy storage.",
        "Bob, a policy analyst, advises the city council on energy regulation. "
        "He previously worked at Northfield University.",
        "The 2024 grid modernization bill funds battery storage projects. "
        "Acme Labs testified in support of the bill.",
    ]
    submission = service.add_text_batches(
        graph_id,
        chunks,
        batch_size=10,
        progress_callback=lambda msg, p: print(f"    {int(p * 100):3d}% {msg}", flush=True),
    )
    check("batch submission returned episode uuids", len(submission.episode_uuids) == len(chunks),
          f"episodes={len(submission.episode_uuids)}")

    # the exact call api/graph.py build_task makes after submission
    wait_result = service._wait_for_batch(
        submission,
        lambda msg, p: print(f"    wait: {msg}", flush=True),
    )
    check("_wait_for_batch terminal + item validation", len(wait_result) == len(chunks),
          f"items={len(wait_result)}")

    step("async ingestion (updater path: graph.add returns immediately)")
    ns1 = service.client.graph.add(
        graph_id=graph_id,
        data="Carol joined Acme Labs as a compliance officer and reports to Alice.",
        created_at="2026-11-08T00:10:00Z",
        source_description="MiroFish simulation activity batch",
        metadata={"source": "verify", "platform": "twitter"},
    )
    ns2 = service.client.graph.add(
        graph_id=graph_id,
        data="Acme Labs opened a Singapore branch with Carol as its first hire.",
        created_at="2026-11-08T00:11:00Z",
        source_description="MiroFish simulation activity batch",
        metadata={"source": "verify", "platform": "reddit"},
    )
    check("graph.add returned uuids immediately", bool(ns1.uuid_) and bool(ns2.uuid_))

    pending = {ns1.uuid_, ns2.uuid_}
    deadline = time.time() + 240
    while pending and time.time() < deadline:
        for uuid_ in list(pending):
            if getattr(service.client.graph.episode.get(uuid_=uuid_), "processed", False):
                pending.remove(uuid_)
        if pending:
            time.sleep(3)
            print(f"    async pending: {len(pending)}", flush=True)
    check("async episodes processed (processed poll is real)", not pending,
          f"pending={len(pending)}")

    step("graph statistics + entity reads")
    info = service.client.graph.get(graph_id)
    check("nodes extracted", info.node_count >= 3, f"nodes={info.node_count}")
    check("edges extracted", info.edge_count >= 1, f"edges={info.edge_count}")

    nodes_page = service.client.graph.node.with_raw_response.get_by_graph_id(graph_id, limit=100)
    names = [n.name for n in nodes_page.data]
    print(f"    entities: {names}", flush=True)
    check("expected entities present", any("Alice" in n for n in names) and any("Acme" in n for n in names))

    reader = ZepEntityReader(api_key=None)
    filtered = reader.filter_defined_entities(
        graph_id=graph_id, defined_entity_types=None, enrich_with_edges=False
    )
    check("entity reader filtered count", filtered.filtered_count >= 2,
          f"count={filtered.filtered_count}")

    step("search (embeddings + fulltext)")
    tools = ZepToolsService(api_key=None)
    result = tools.quick_search(graph_id, "Who works at Acme?", limit=5)
    check("quick_search returns facts", len(result.facts) > 0, f"facts={len(result.facts)}")
    print(f"    sample fact: {result.facts[0][:120] if result.facts else '-'}", flush=True)

    step("cleanup: delete")
    service.delete_graph(graph_id)
    try:
        service.client.graph.get(graph_id)
        check("graph deleted (get must 404)", False)
    except Exception as exc:
        check("graph deleted (get must 404)", getattr(exc, "status_code", None) == 404,
              f"status={getattr(exc, 'status_code', None)}")

    step("result")
    if FAILED:
        print("VERIFY FAILED — see FAIL lines above")
        return 1
    print(f"ALL CHECKS PASSED (data dir: {_TMPDIR})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        print("\nVERIFY FAILED — unhandled exception")
        sys.exit(1)
