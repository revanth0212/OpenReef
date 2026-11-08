# Getting started with OpenReef

OpenReef runs anywhere Docker runs, plus natively on macOS 26+ Apple
silicon via Apple's `container` CLI. One container, no external services
required for the default experience.

## What you need

- **Docker** (Linux/Windows/macOS) — or — **macOS 26+ on Apple silicon**
  with the [container CLI](https://github.com/apple/container) installed
  (`container system start` done once).
- **An LLM provider** with an OpenAI-compatible **chat** endpoint
  (OpenAI, Cloudflare Workers AI, a local MLX server like
  [oMLX](https://github.com/jundot/omlx), anything similar).
- That's it. Memory is native by default — **no Zep account, no Zep key.**

## Path A — Docker (recommended, any OS)

```bash
git clone https://github.com/revanth0212/OpenReef.git
cd OpenReef
cp .env.example .env
# edit .env: LLM_BASE_URL, LLM_API_KEY, LLM_MODEL_NAME
docker compose up -d --build
```

Open **http://localhost:3000** (backend on :5001). Logs:
`docker compose logs -f`. Data persists in `./backend/uploads` (seed docs,
simulations, reports, and the ReefMemory store). `docker compose down`
stops; deleting `./backend/uploads` resets everything.

## Path B — Apple containers (macOS 26+, no Docker daemon)

Same image, one command (builds on first run):

```bash
git clone https://github.com/revanth0212/OpenReef.git
cd OpenReef
cp .env.example .env        # fill in your LLM config
./scripts/run-apple-container.sh
```

`stop` / `logs` / `wipe` work as subcommands; overrides documented in the
script header. Requires the [container CLI](https://github.com/apple/container)
(`container system start` once).

## Local models (oMLX, llama.cpp, LM Studio…)

Point `LLM_BASE_URL` at your server (`http://host.docker.internal:8000/v1`
or `http://host.container.internal:8000/v1`) — OpenReef **auto-detects
local endpoints** and adapts requests in-process (root-object JSON schema,
token cap, repetition penalty). No shim, nothing to enable. See
[docs/local-models.md](docs/local-models.md) for requirements, tested
models, and limitations.

## First run & first simulation

1. Open the UI — it appears in your browser's language (English by default).
2. Upload a seed document (PDF/Markdown/text) — e.g. describe a scenario.
3. Enter a simulation requirement ("what happens if…"), build the graph.
4. Prepare the environment, run a short simulation (you can cap rounds in
   the UI), then generate the report.
5. Refresh the page at any point — the simulation keeps running and the UI
   re-syncs.

## Optional features (all opt-in)

| Feature | How to enable |
|---|---|
| **Multi-LLM** (per-stage models, failover chains) | Create `llm.config.json` next to `.env` — copy `llm.config.example.json`. See [multi-llm.md](multi-llm.md). |
| **Agent web search** (Tavily) | Add `TAVILY_API_KEY=tvly-...` to `.env`, then enable per simulation in the UI (Step 2 toggle). See [web-search.md](web-search.md). |
| **Local embeddings** (e.g. oMLX serving `embeddinggemma`) | Set `REEF_MEMORY_EMBEDDER_*` in `.env`. The vector dimension is probed automatically. See [reef-memory.md](reef-memory.md). |
| **Legacy Zep Cloud memory** | Set `MEMORY_BACKEND=zep` and provide `ZEP_API_KEY`. |

## Troubleshooting

- **"Cannot determine the embedding vector dimension automatically"** —
  your provider doesn't expose an embeddings endpoint, or it was
  unreachable at first use. Set `REEF_MEMORY_EMBEDDER_MODEL`,
  `REEF_MEMORY_EMBEDDER_BASE_URL`, `REEF_MEMORY_EMBEDDER_API_KEY` (and
  optionally `REEF_MEMORY_EMBEDDER_DIM`) to a provider that serves
  embeddings.
- **Report/simulation quality** — the default model matters. Small local
  models work for short runs; ontology extraction benefits from a capable
  model. Use `llm.config.json` to route heavy stages to stronger models.
- **It behaves like old code** (mentions features you removed, ignores new
  config) — the runner rebuilds on every start by default for exactly this
  reason; if you used `OPENREEF_SKIP_BUILD=1`, rebuild once without it.
- Everything else: see [testing.md](testing.md) for the full verification
  matrix and log locations.
