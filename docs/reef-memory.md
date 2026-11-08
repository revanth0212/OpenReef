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

Extraction uses your configured provider (the `ontology` chain head from
`llm.config.json`, or the legacy `LLM_*` vars). Embeddings default to the
same provider but can live on a **different provider entirely**:

```bash
REEF_MEMORY_EMBEDDER_MODEL=text-embedding-3-small   # default
REEF_MEMORY_EMBEDDER_BASE_URL=                      # optional: other provider
REEF_MEMORY_EMBEDDER_API_KEY=                       # optional: other key
```

Example — extract with a local model, embed with OpenAI:

```bash
REEF_MEMORY_EMBEDDER_BASE_URL=https://api.openai.com/v1
REEF_MEMORY_EMBEDDER_API_KEY=${OPENAI_API_KEY}
REEF_MEMORY_EMBEDDER_MODEL=text-embedding-3-small
```

Example — everything local with oMLX (embeddings served by the same
oMLX instance, bypassing the chat shim):

```bash
REEF_MEMORY_EMBEDDER_BASE_URL=http://host.container.internal:8000/v1
REEF_MEMORY_EMBEDDER_API_KEY=root
REEF_MEMORY_EMBEDDER_MODEL=embeddinggemma-2-8bit
```

**Vector dimension**: measured automatically — at first use ReefMemory
sends one tiny embedding request and counts the dimensions, so any model
works with no bookkeeping. To skip the probe (offline starts, strict
setups), set it yourself:

```bash
REEF_MEMORY_EMBEDDER_DIM=768
```

## Configuration

```bash
# .env — nothing needed for the default native backend
# optional overrides:
#REEF_MEMORY_DIR=/app/backend/uploads/reef-memory
#REEF_MEMORY_EMBEDDER_MODEL=text-embedding-3-small
#MEMORY_BACKEND=zep            # only if you explicitly want Zep Cloud
```

## Ingestion behavior

Two paths, mirroring how MiroFish used Zep:

- **Graph build** (`batch.add`) is synchronous end-to-end: chunk extraction
  completes before the batch reports `succeeded`, so the build's
  `_wait_for_batch` finishes in one poll tick.
- **Simulation activity** (`graph.add`, the updater path) is **asynchronous**
  like Zep Cloud: `add` returns a pre-assigned episode uuid immediately, a
  daemon worker drains the queue, groups same-graph episodes into combined
  bulk extraction (`use_combined_extraction`, up to 8 per call), and the
  updater's drain polls the real `processed` flag. Failed episodes retry
  once, then go terminal-failed (logged loudly) so a drain can never wedge.

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
- `backend/tests/test_memory_contract.py` — walks all backend sources and
  fails if any call-site method is missing from the compat client.
- **Real end-to-end verification** (no mocks — real Graphiti, Kuzu, and
  your configured providers; cents of LLM cost):
  ```bash
  cd backend && uv run python scripts/verify_reef_memory.py
  ```
  Drives the exact service layer the app uses: graph create → set_ontology
  → batch ingestion → entity reads → search (embeddings) → delete.
  Run this before asking anyone to test in a container.
- Container smoke: run a normal simulation with no `ZEP_API_KEY` in
  `.env` — graph build, agent memory, report search, and the graph panel
  should all work; `uploads/reef-memory/` appears on the host.
