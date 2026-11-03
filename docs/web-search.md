# Web search for agents (OpenReef)

Agents and the report agent can search the live web (via
[Tavily](https://app.tavily.com)) while working on their responses — for
real-world context the simulation doesn't contain: news, market data,
public events, policy documents.

Fully optional. Without `TAVILY_API_KEY`, nothing changes: the tools are
absent and every code path is a clean no-op. Tavily is a plain HTTPS API —
no extra containers, identical behavior in Docker and Apple containers.

## Setup

```bash
# .env
TAVILY_API_KEY=tvly-...
```

Free tier is 1000 credits/month; each search here costs 1 credit
(`search_depth: basic`, max 10 results per call, default 5).

## Enable per simulation

The toggle lives in the UI (Step 2, "Enable agent web search") and in the
prepare API:

```json
POST /api/simulation/prepare
{ "simulation_id": "...", "web_search_enabled": true }
```

This writes `"web_search": {"enabled": true}` into `simulation_config.json`
and does two things:

1. **OASIS agents** get a `web_search` tool (camel `FunctionTool` attached
   after agent-graph creation in `scripts/run_*_simulation.py`). Agents call
   it like any other tool when they need real-world facts.
2. **Report agent** gains a fifth ReACT tool `web_search` — the section
   writer can ground predictions in current events. The tool appears in the
   tool list only when the simulation enabled it **and** the key is set.

## Behavior notes

- Failures never break a simulation: tool-attach errors are logged and the
  sim continues without search; search errors return a text message the
  agent can read.
- Rate limiting is bounded by design: `max_results` is clamped to 10, the
  query is single-shot (no auto-refetch loops).
- Agents decide themselves when to search — watch agent logs if usage
  surprises you, and set Tavily spend limits on your account.

## Current limitations

- Search results enter agent context but are **not** persisted into the Zep
  graph (they're working context, not simulated-world facts).
- Per-agent enable/disable is not granular: the flag covers all agents in
  the simulation.
