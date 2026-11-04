# Multi-LLM support (OpenReef)

OpenReef can drive several OpenAI-compatible LLM providers at once:

- **Stage-based selection** — use a cheap model for ontology extraction, a strong
  model for reports, a local model for the OASIS simulation, each independently.
- **Automatic failover** — when the primary provider fails (rate limits, 5xx,
  timeouts, bad credentials), calls fail over to the next provider in the chain.

Everything is opt-in. With no registry configured, OpenReef behaves exactly like
MiroFish: one provider from `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL_NAME`.

## Configuration

Put an `llm.config.json` next to your `.env` (same place you configure the
LLM today) — the OpenReef run script mounts it into the container
read-only at `/app/backend/llm.config.json`, and the backend picks it up
automatically. In development, drop it at `backend/llm.config.json`.

Priority order (first match wins):

1. `LLM_PROVIDERS_JSON` — inline JSON env var (Docker-only escape hatch;
   clunky, which is why it's last resort).
2. `LLM_PROVIDERS_FILE` — explicit path to a JSON file anywhere.
3. `llm.config.json` — the convention (auto-detected). **Recommended.**

```json
{
  "providers": [
    {
      "name": "cloudflare-gemma",
      "base_url": "https://api.cloudflare.com/client/v4/accounts/<id>/ai/v1",
      "api_key": "${CF_API_TOKEN}",
      "model": "@cf/google/gemma-4-26b-a4b-it",
      "enabled": true
    },
    {
      "name": "openai-backup",
      "base_url": "https://api.openai.com/v1",
      "api_key": "${OPENAI_API_KEY}",
      "model": "gpt-4.1-mini"
    }
  ],
  "chains": {
    "default":    ["cloudflare-gemma", "openai-backup"],
    "ontology":   ["cloudflare-gemma"],
    "report":     ["openai-backup", "cloudflare-gemma"]
  }
}
```

Notes:

- `api_key` may be a literal or an env reference (`${VAR}`) so secrets can stay
  in the environment.
- A stage with no chain entry uses `default`; with no registry at all, every
  stage uses the legacy `LLM_*` provider.
- Disabled providers (`"enabled": false`) are skipped in every chain.

## Stages

| Stage | What runs there |
|---|---|
| `ontology` | Knowledge-graph/ontology extraction from seed docs |
| `profile` | OASIS agent persona generation |
| `config` | Simulation config generation |
| `simulation` | The OASIS simulation subprocess (env overlay at spawn) |
| `report` | Report planning, section writing, report chat |
| `default` | Everything else (zep tools, etc.) |

## Failover behavior

- Provider errors (429/5xx/timeouts/connection/auth) fail over to the next chain
  entry; if every entry fails, the last error is raised.
- Content problems (unparseable JSON) are first retried within the provider
  (existing bounded retry), then fail over.
- JSON-mode negotiation (`response_format`) is per-provider: a provider that
  rejects JSON mode is downgraded for that call only; the next provider gets a
  fresh attempt.

## Management API

- `GET /api/llm/providers` — registry state and chains (API keys are never returned).
- `POST /api/llm/providers/test` — `{"base_url", "api_key", "model"}` →
  `{"ok", "latency_ms", "error"}` connectivity check.

## Current limitations

- The OASIS simulation uses the chain **head** for its stage; assigning different
  models to individual agents inside one simulation needs OASIS-side support and
  is tracked as a spike in the roadmap.
- `oasis_profile_generator` / `simulation_config_generator` use the stage's chain
  head (no mid-call failover yet); `LLMClient`-based services fail over fully.
