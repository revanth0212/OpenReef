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
| 7 | **ReefMemory** — native graph memory (Graphiti + embedded Kuzu), Zep optional | core done | `app/memory/`, `config.py` | default; `MEMORY_BACKEND=zep` for legacy |
| 1 | **Multi-LLM** — provider registry, per-stage chains, failover | done | `app/utils/llm_providers.py`, `llm_client.py`, `app/api/llm.py` | `llm.config.json` / `LLM_PROVIDERS_*` |
| 2 | **Web search** — Tavily tool for sim agents + report agent | done | `app/utils/web_search.py`, sim scripts, `report_agent.py` | `TAVILY_API_KEY` + per-sim toggle |
| 3 | **End-to-end English** — reports in English (UI i18n existed upstream) | done | `report_agent.py` (`*_EN` templates) | follows UI locale |
| — | **Step-3 resilience** — no force-restart on refresh, rehydrate, visibility catch-up | done | `frontend/.../Step3Simulation.vue` | always on |
| — | **Local-model adapter** — auto root-JSON schema, token cap, rep penalty (replaces the old shim) | done | `app/openreef/localmodels.py` | auto via endpoint heuristic; `OPENREEF_LOCAL_LLM` override |
| — | **Apple-container runner** | done | `scripts/run-apple-container.sh` | manual |
| 4 | Report customization (length/tone/complexity presets) | pending | — | — |
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
| `api/simulation.py` | prepare API accepts `web_search_enabled` | 🟢 |
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
- Ontology-enforced typed extraction (v1 stores ontology, free extraction)
- Zep Cloud → ReefMemory data migration tool
- Publishing our own multi-arch images (after feature set stabilizes)
- Per-agent model mix inside one simulation (needs OASIS-side spike)
- Features 4 (report customization), 5 (report formats), 6 (live view)
