# ReefMemory — native graph memory (OpenReef)

OpenReef replaces the mandatory Zep Cloud dependency with **memory that
lives with the tool**: the [Graphiti](https://github.com/getzep/graphiti)
engine (Apache-2.0 — Zep's own open-source core: temporal knowledge graph,
entity/edge extraction, hybrid semantic search) running on an **embedded
Kuzu** graph database. Zero external services, zero extra API keys, data
under `backend/uploads/reef-memory/` (inside your existing data volume).

## What this means for you

- **No Zep account, no Zep key.** The default `MEMORY_BACKEND=graphiti`
  works out of the box with only your LLM config.
- **Single-container unchanged.** No new services in Docker or Apple
  containers; the embedded store is a local file.
- **Zep becomes opt-in, not required.** Set `MEMORY_BACKEND=zep` +
  `ZEP_API_KEY` to use Zep Cloud exactly like before (kept for existing
  setups and comparison testing).

## How it works

MiroFish talks to Zep through a compact SDK surface (`graph.create/add/
search/node.get/…`). ReefMemory implements that same duck-typed client
(`app/memory/graphiti_client.py`) over Graphiti, so graph construction,
agent search, simulation-time ingestion, and the UI graph panel all work
unchanged. `app/memory/factory.py` swaps implementations via
`MEMORY_BACKEND`:

| Backend | Needs | Data lives |
|---|---|---|
| `graphiti` (default) | LLM config only (extraction + embeddings) | `uploads/reef-memory/` |
| `zep` | `ZEP_API_KEY` | Zep Cloud |

Extraction and embeddings use your configured provider (the `ontology`
chain head from `llm.config.json`, or the legacy `LLM_*` vars). The
provider must offer an OpenAI-compatible **chat** and **embeddings**
endpoint (e.g. OpenAI, or any endpoint serving both). Override the
embedding model with `REEF_MEMORY_EMBEDDER_MODEL` (default
`text-embedding-3-small`).

## Configuration

```bash
# .env — nothing needed for the default native backend
# optional overrides:
#REEF_MEMORY_DIR=/app/backend/uploads/reef-memory
#REEF_MEMORY_EMBEDDER_MODEL=text-embedding-3-small
#MEMORY_BACKEND=zep            # only if you explicitly want Zep Cloud
```

## Current limitations (v1)

- **Ontology types are stored, not yet enforced.** The generated ontology
  is kept with each graph; extraction currently uses Graphiti's free
  schema (typed extraction from the ontology is the next refinement).
- **Sequential batch ingestion.** `batch.add` processes seed-document
  chunks one at a time (correctness first; throughput tuning later).
- **Kuzu driver deprecation.** graphiti-core marks the Kuzu driver
  deprecated upstream; the migration path is FalkorDB (a small driver
  swap) when needed. Your data remains exportable JSON/Cypher either way.
- **Reranking is identity-based** so search works on any provider; an
  opt-in provider reranker may come later.

## Testing

- `backend/tests/test_reef_memory.py` — mapping, lifecycle, backend
  selection, and a real embedded-Kuzu lifecycle test (no network).
- Container smoke: run a normal simulation with no `ZEP_API_KEY` in
  `.env` — graph build, agent memory, report search, and the graph panel
  should all work; `uploads/reef-memory/` appears on the host.
