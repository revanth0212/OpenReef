# Local models with OpenReef

OpenReef has first-class support for local OpenAI-compatible model
servers — oMLX, llama.cpp, LM Studio, vLLM-on-lan, etc. There is **no shim
to install and nothing extra to configure**: when your `LLM_BASE_URL`
points at a local endpoint, OpenReef detects it automatically and adapts
its requests in-process.

## How to point OpenReef at a local model

The endpoint must be reachable from **inside the container**:

| Where the model server runs | `LLM_BASE_URL` inside `.env` |
|---|---|
| Same machine, Docker Desktop | `http://host.docker.internal:8000/v1` |
| Same machine, Apple containers | `http://host.container.internal:8000/v1` |
| Another machine on your network | `http://<host>.local:8000/v1` |

Apple containers need a one-time DNS setup so containers can reach the
host (requires admin; see
[Apple's docs](https://github.com/apple/container/blob/main/docs/host-integration.md)):

```bash
sudo container system dns create host.container.internal --localhost 203.0.113.113
```

`LLM_API_KEY` can be any non-empty string for servers without auth
(oMLX with auth: use the key from its `settings.json`).

## What "local mode" changes, and why

Local servers earn their keep on chat, but small models hit three known
failure modes on MiroFish's structured-JSON workload. The adapter
(activated per provider, so mixed failover chains adapt only their local
members) applies the proven fixes — this is the same behavior our earlier
external shim provided, minus the proxy:

1. **Root-object JSON schema.** Many local servers map `json_object`
   mode to "any JSON value", so small models wrap answers in an array —
   which MiroFish rejects. Local mode requests a JSON *schema* constrained
   to a root object instead. (Observed: Qwen3.5-4B wrapped ontology output
   in an array 10/10 times without this; with it, complete valid graphs.)
2. **Token cap (4096).** Without a cap, a rambling model generates to the
   server's ~32k limit and fails after minutes. Capped, it fails in
   seconds — and with (1), it usually doesn't fail at all.
3. **Repetition penalty (1.05).** Breaks repetition loops small models
   fall into on long structured outputs.

You'll see one log line when it activates:
`OpenReef local-model mode: request adaptation active for ...`.

## Requirements and limitations

- **Server must accept** `response_format` with `json_schema` and the
  `repetition_penalty` extension. Tested: oMLX. llama.cpp and LM Studio
  accept both. If your server rejects them, set `OPENREEF_LOCAL_LLM=off`
  in `.env` (requests then pass through unmodified).
- **Model quality matters more than size here.** Tested locally:
  `Qwen3.5-4B-MLX-4bit` completes real ontology prompts (~22s, rich
  output) with the adapter; the 0.8B variant rambles past the cap on real
  ontology inputs; a 27B variant echoed prompts. Vet a new model with a
  short run before committing a long simulation — model behavior here is
  not predictable from parameter count.
- **Throughput is the real cost.** Simulations make many sequential LLM
  calls; a local model is free and private but slow. Use short sims, or
  route heavy stages to a hosted model via `llm.config.json` (local for
  simulation agents, hosted for ontology/report is a popular split).
- **Embeddings for ReefMemory** need a separate embeddings-capable model.
  oMLX can serve one (e.g. `embeddinggemma-2-8bit`) — see
  [reef-memory.md](reef-memory.md) for the three `REEF_MEMORY_EMBEDDER_*`
  lines. The dimension is probed automatically.

## Disabling

```bash
OPENREEF_LOCAL_LLM=off   # pass requests through unmodified
OPENREEF_LOCAL_LLM=on    # force on even for hosted-looking endpoints
```

Default is `auto`: loopback hosts (`localhost`, `127.0.0.1`,
`host.container.internal`, `host.docker.internal`) and RFC-2606-style
local names (`.local`, `.internal`, `.lan`, `.home.arpa`) count as local.
