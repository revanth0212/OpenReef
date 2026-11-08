# OpenReef — the living fork document

> **Purpose**: this document is the contract between OpenReef and upstream
> MiroFish. It records what OpenReef is, what it stands for, the policies
> every change must obey, and — most importantly for rebasing — an
> inventory of every deliberate modification to upstream files so the next
> `git merge upstream/main` can tell *"merge carefully, this is ours"* from
> *"safe to take upstream's version"*.
>
> **Maintained**: update this file in the same commit as any change it
> describes. If you touch an upstream file and this document doesn't know,
> the rebase will hurt.

*Last full review: feature/7-reef-memory (Nov 2026).*

---

## 1. What OpenReef is — and stands for

**Name**: MiroFish is the fish; OpenReef is the ecosystem it swims in.
"Open" is the mission: **run the whole engine on your own infrastructure,
end to end, without being tied to anyone's cloud.**

OpenReef is an **enhancement fork**, not a replacement. Same engine, same
philosophy as MiroFish — plus capabilities upstream doesn't have. A user
who ignores every OpenReef extra gets behavior **identical** to MiroFish.

## 2. Fundamentals (non-negotiable)

1. **Default behavior never changes.** Every feature is opt-in and inert
   without configuration: no registry → single-provider LLM; no
   `TAVILY_API_KEY` → no search tooling; `MEMORY_BACKEND` default is native
   but lazily initialized; the local-model adapter activates only for
   local endpoints. Existing MiroFish `.env` files keep working untouched.
2. **One container, always.** Everything must run inside the unchanged
   single-container image. New required services are forbidden; optional
   extras prefer embedded > sidecar > external.
3. **The UI never loses work.** Server-side state is the truth; the UI
   rehydrates on every mount; refreshing or closing a tab must never
   destroy in-flight simulations.
4. **Plugins over upstream edits.** Additions live in OpenReef-namespaced
   modules (`app/openreef/`, `app/memory/`, `llm_providers.py`, …).
   Upstream files receive only minimal, labeled hook lines. Everything
   OpenReef adds is greppable: search for `OpenReef` / `reef`.
5. **Boring merges.** Upstream syncs happen deliberately, at development
   time — never at build/runtime. Fewer touched lines = cheaper merges
   (see §4 for the exact inventory).
6. **English for everything we author.** New files and touched lines are
   English; upstream's Mandarin comments stay as-is (translating them is
   merge-conflict debt with low payoff). User-facing runtime text goes
   through the existing `locales/*.json` i18n system.
7. **Reuse, don't reinvent.** Graphiti (memory), OASIS/camel tool APIs
   (agent tools), httpx (HTTP), the existing `llm.config.json`-style file
   conventions — existing tooling beats custom code.
8. **Tests and docs ship with the feature.** Backend pytest green (CI runs
   on the fork), frontend builds, and docs/ + this file updated in the
   same commit.

## 3. Feature inventory

| # | Feature | State | Lives in | Enabled by |
|---|---------|-------|----------|-----------|
| 7 | **ReefMemory** — native graph memory (Graphiti + embedded Kuzu), Zep optional. Sync batch build + async updater ingestion (combined bulk extraction), typed extraction, real `processed` polling | done | `app/memory/`, `config.py` | default; `MEMORY_BACKEND=zep` for legacy |
| 1 | **Multi-LLM** — provider registry, per-stage chains, failover | done | `app/utils/llm_providers.py`, `llm_client.py`, `app/api/llm.py` | `llm.config.json` / `LLM_PROVIDERS_*` |
| 2 | **Web search** — Tavily tool for sim agents + report agent | done | `app/utils/web_search.py`, sim scripts, `report_agent.py` | `TAVILY_API_KEY` + per-sim toggle |
| 3 | **End-to-end English** — reports in English (UI i18n existed upstream) | done | `report_agent.py` (`*_EN` templates) | follows UI locale |
| — | **Step-3 resilience** — no force-restart on refresh, rehydrate, visibility catch-up | done | `frontend/.../Step3Simulation.vue` | always on |
| — | **Local-model adapter** — auto root-JSON schema, token cap, rep penalty (replaces the old shim) | done | `app/openreef/localmodels.py` | auto via endpoint heuristic; `OPENREEF_LOCAL_LLM` override |
| — | **Apple-container runner** | done | `scripts/run-apple-container.sh` | manual |
| — | **Real-component verifier** — pre-flight gate for the memory backend (no mocks) | done | `backend/scripts/verify_reef_memory.py` | manual; must pass before container tests |
| 4 | **Report customization** — presets, tone/audience/complexity/length, per-report language override | done | `app/openreef/report_options.py`, `report_agent.py`, `api/report.py`, Step 3 options panel | per-report, optional (default = upstream) |
| 6 | ReefLive (watch agents work) | pending | — | — |
| 5 | ReefReport (HTML/PDF/infographics) | pending | — | — |
| 7.x | FalkorDB driver, typed-ontology extraction, Zep→Graphiti migration | deferred | — | — |

## 4. Upstream files we deliberately changed (rebase care list)

**Legend — care level**: 🟢 safe (tiny/additive; take upstream's side and
re-apply ours) · 🟡 careful (real edits; read the merge) · 🔴 core surgery
(merge by hand, run tests)

### Configuration / packaging
| File | Change | Care |
|---|---|---|
| `.env.example` | Full English rewrite + OpenReef option blocks (keys unchanged) | 🟡 upstream edits it occasionally — re-apply English, keep keys |
| `docker-compose.yml` | Builds from source by default (was upstream prebuilt image) | 🟡 keep `build: .` |
| `README.md` | OpenReef banner above upstream content | 🟢 |
| `pyproject.toml` / `requirements.txt` / `uv.lock` | graphiti-core + kuzu deps; `[tool.uv] override-dependencies = ["neo4j>=5.26.0"]` (camel-oasis pins 5.23; its neo4j path is unused) | 🟡 preserve the override — removing it silently re-resolves graphiti to an ancient version |
| `.github/workflows/test.yml` | 🆕 fork-only pytest CI | 🟢 new file |

### Backend — core LLM plumbing
| File | Change | Care |
|---|---|---|
| `app/utils/llm_client.py` | Provider-chain failover refactor; `object.__new__` legacy-test contract preserved via `_LEGACY_ENTRY` sentinel | 🔴 core surgery — upstream changes here must be re-merged by hand, then run `test_llm_json_responses.py` (upstream's contract tests) + `test_llm_client_failover.py` |
| `app/utils/openai_chat_compat.py` | One labeled hook line (`maybe_adapt_for_client`) for the local-model adapter | 🟡 re-apply the hook after taking upstream's param logic |
| `app/utils/llm_providers.py` | 🆕 registry/chains/env-overlay | 🟢 new file |
| `app/openreef/localmodels.py` | 🆕 local-model detection + adaptation | 🟢 new file |
| `app/config.py` | `MEMORY_BACKEND`; `validate()` requires ZEP key only for zep backend | 🟢 |

### Backend — memory services (all get `get_memory_client()` swap + key-guards)
| File | Change | Care |
|---|---|---|
| `services/graph_builder.py` | factory swap; `memory_backend_name()` in key guard | 🟡 |
| `services/zep_tools.py` | factory swap; key guard | 🟡 |
| `services/zep_entity_reader.py` | factory swap; key guard | 🟡 |
| `services/zep_graph_memory_updater.py` | factory swap; key guard | 🟡 |
| `services/oasis_profile_generator.py` | factory swap (runs even without ZEP key when native); `chain_head_credentials("profile")` | 🟡 |
| `services/simulation_runner.py` | `subprocess_env_overlay("simulation")` at OASIS spawn | 🟢 |
| `services/ontology_generator.py` | `LLMClient(stage="ontology")` | 🟢 |

### Backend — web search threading
| File | Change | Care |
|---|---|---|
| `services/simulation_manager.py` | `web_search_enabled` param threading | 🟢 |
| `services/simulation_config_generator.py` | `SimulationParameters.web_search_enabled` + serialization | 🟢 |
| `api/simulation.py` | prepare API accepts `web_search_enabled`; three ZEP-key gates now backend-conditional | 🟢 |
| `api/graph.py` | three ZEP-key gates (build/data/delete) now backend-conditional via `memory_backend_name()` | 🟢 |
| `scripts/run_twitter_simulation.py`, `run_reddit_simulation.py` | `_maybe_add_web_search_tool()` (camel `FunctionTool` attach) | 🟡 upstream revises these scripts — re-apply the call + method |
| `tests/test_zep_graph_memory_updater.py` | Fake-client injection updated for the factory | 🟢 |

### Backend — report agent (largest single-file change)
| File | Change | Care |
|---|---|---|
| `services/report_agent.py` | EN template set (`*_EN`, picked when locale=en); `stage="report"`; conditional `web_search` tool (zh/en descs + advice line); `_pick_template` helper | 🟡 additive, but big — if upstream edits prompts, take theirs and re-apply our EN variants; placeholders must stay in sync (enforced by `test_report_agent_locale.py`) |

### Frontend
| File | Change | Care |
|---|---|---|
| `locales/en.json`, `locales/zh.json` | New key blocks (`providers.*`, `step2.webSearchToggle`, `log.simStateRecovered*`, `log.finalizingGraphSync`) | 🔴 both sides add keys — merge both, keep key parity (CI locale test + `npm run build` catch gaps) |
| `components/Step3Simulation.vue` | Resilience: `resumeSimulationState`, visibility catch-up, finalizing log; mount no longer force-starts | 🟡 upstream may restructure this file — preserve the rehydrate flow |
| `components/Step2EnvSetup.vue` | Providers panel + web-search toggle + locales | 🟡 |
| `i18n/index.js` | Browser-language detection for first visit | 🟢 |
| `api/llm.js` | 🆕 | 🟢 |

## 4b. Design decisions and justifications (why, not what)

Decisions made during implementation, recorded so future changes don't
accidentally undo them. Each entry is the conclusion of a real failure or
trade-off — read before redesigning any of it.

### Memory backend (ReefMemory)

- **Duck-typed compat client, not call-site rewrites.** MiroFish talks to
  Zep through ~17 methods on 6 namespaces. Rewiring every service would
  touch all five memory services + API layer; instead
  `app/memory/graphiti_client.py` implements the same surface over
  Graphiti, so upstream code runs unmodified. Merge cost: one factory
  call per service. This is the single most important structural decision.
- **Graphiti (Apache-2.0) as the engine — Zep's own open-source core.** Not
  a hand-rolled KG: entity/edge extraction, temporal invalidation, and
  hybrid search come from the team that built Zep, so semantics stay close
  to what MiroFish expects.
- **Embedded Kuzu over FalkorDB/Neo4j** (for v1): memory "lives with the
  tool" — one file under `uploads/reef-memory/`, zero new services, single
  container unchanged. Accepted cost: the Kuzu driver is deprecated
  upstream in graphiti, so we bridge its gaps **in our code** (`_database`/
  `clone` scope shim, FTS index bootstrap). FalkorDB remains the scale-out
  path (driver swap, not redesign). We deliberately did NOT fork graphiti.
- **Dedicated background event loop for all graphiti calls.** The first
  attempt used `asyncio.run()` per call; graphiti's async HTTP clients pool
  connections per loop, so pooled connections died with "Event loop is
  closed" on reuse (2-chunk tests pass, 25-chunk docs failed). One daemon
  loop keeps pools valid. Lesson encoded in `test_all_operations_share_one_
  event_loop`.
- **Two ingestion paths, mirroring Zep's actual contract.** Graph build
  (`batch.add`) is synchronous (build's `_wait_for_batch` expects to poll a
  terminal batch status — the vocabulary is `succeeded/partial/failed/
  invalid/canceled`, NOT "processed"; getting this wrong cost a 600s
  timeout). Simulation activity (`graph.add`) is fire-and-forget with a
  pre-assigned uuid + a daemon worker that groups same-graph episodes into
  `add_episode_bulk(use_combined_extraction=True)` — one extraction pass
  per flush instead of one per episode (the real wall-clock win; ~15s →
  ~30s for a whole batch). The updater's drain polls a REAL `processed`
  flag (pending→False, done→kuzu-verified True, failed→terminal True after
  one retry — a drain can never wedge).
- **Typed extraction from the stored ontology.** `set_ontology` registers
  the live pydantic classes in a process registry; `add_episode` passes
  them as `entity_types`/`edge_types`. Free extraction left nodes unlabeled,
  which silently broke `ZepEntityReader`'s label-based filtering (0
  entities). Typed extraction also found more edges. Registry is
  process-lifetime; the JSON snapshot in meta is informational only.
- **Identity cross-encoder.** Graphiti requires a cross-encoder at
  construction and defaults to OpenAI's, which breaks providers without a
  rerank endpoint. A neutral identity reranker keeps search working on any
  OpenAI-compatible endpoint; a provider reranker stays a possible opt-in.
- **Probe-based embedding dimensions.** No model→dim lookup table (a
  maintenance liability — models change daily); one tiny `/embeddings` call
  at init measures it. `REEF_MEMORY_EMBEDDER_DIM` is the explicit override
  for unreachable endpoints.
- **Independent embedder provider.** Extraction rides the ontology chain;
  embeddings default to the same provider but can live elsewhere (e.g.
  hosted chat + local oMLX embeddings) via three env vars — mixed setups
  are a first-class use case, not an edge case.

### Methodology (the expensive lessons)

- **`backend/scripts/verify_reef_memory.py` is the pre-flight gate.** It
  drives the real service layer (GraphBuilderService/ZepEntityReader/
  ZepToolsService) against real Graphiti+Kuzu+configured providers — no
  mocks. Rule: it must pass locally BEFORE anyone is asked to test in a
  container. It exists because every compat bug that reached the user
  (batch vocabulary, `processed` flag, page shapes, arg capture, event
  loop) was invisible to unit tests with assumption-shaped fakes.
- **Audit call sites, not SDK docs.** Each miss came from implementing
  against the SDK's happy-path surface instead of the call sites' lifecycle
  behavior. `tests/test_memory_contract.py` now walks all backend sources
  and fails if any invoked client method is missing from the compat surface.
- **State machines discovered (Zep compat contract)** — if the compat is
  ever reworked, these are the exact semantics callers depend on:
  - batch lifecycle `draft → succeeded` (terminal set above); `batch.get`
    must expose `progress.{percent_complete, succeeded_items}`;
    `batch.list/list_items` return page objects (`next_cursor`, `.items`)
  - episodes expose boolean `processed`; `graph.search` returns edges with
    `uuid_/fact/source_node_uuid/target_node_uuid` + a `nodes` list
  - `graph.node.get_edges(node_uuid)`, `with_raw_response.get_by_graph_id`
    return `.data`/`.headers` shapes for the paging layer
  - `set_ontology` receives model CLASSES and `(class, source_targets)`
    edge tuples, not instances
  - node `labels` must include the entity type name (reader filter)
- **The verifier's mirror rule.** Any code path the app calls (e.g.
  `_wait_for_batch`) must be exercised by its REAL method in the verifier,
  not a re-implementation — re-implementing the call hid the batch-vocab bug.

### Presentation & i18n

- Upstream's Mandarin comments stay (merge-cost vs payoff); everything
  OpenReef authors is English; `.env.example` is fully English because it
  is the configuration front door.

## 5. Upstream-sync runbook

```bash
cd OpenReef
git fetch upstream
# mirror into main (read-only upstream tracking branch)
git checkout main && git merge --ff-only upstream/main && git checkout openreef
# deliberate integration point:
git checkout openreef
git merge upstream/main          # conflicts? consult §4 care list
uv run pytest backend/tests -q   # from backend/ — must be green
cd frontend && npm run build     # must pass (locale parity)
# then: update this document's inventory + status log, commit, push
```

Non-negotiables during a sync: never drop the neo4j override, never drop
the `build: .` in compose, always re-apply the two labeled hooks
(`openai_chat_compat`, blueprint registration), and run both test suites
before pushing.

## 6. Known gaps / deferred

- FalkorDB driver option for ReefMemory (scale-out; driver swap)
- Zep Cloud → ReefMemory data migration tool
- Publishing our own multi-arch images (after feature set stabilizes)
- Per-agent model mix inside one simulation (needs OASIS-side spike)
- Features 4 (report customization), 5 (report formats), 6 (live view)
