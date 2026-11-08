# OpenReef Roadmap

> **Status**: research deliverable — nothing here is implemented yet.
> **Method**: full-repo audit (backend `app/`, frontend `src/`, docs) focused on
> one question: *where does OpenReef keep implicit state machines, and what
> would make them better for users, developers, and agents?*
> **Constraint**: every proposal must satisfy the non-negotiables in
> [OPENREEF.md](OPENREEF.md) §2 — default behavior never changes, one container,
> the UI never loses work, plugins over upstream edits, boring merges, English,
> reuse, tests+docs ship with the feature.
> **Last reviewed**: 2026-11-08 (branch `feature/7-reef-memory`).

---

## Part 1 — The state machine inventory

OpenReef has **no formal state machine layer**. Lifecycle is smeared across
enums, boolean flags, string whitelists, file-existence checks, and ad-hoc
derivations in both backend and frontend. Below is every machine found, with
its exact location and its trouble.

### 1A. Backend — explicit (enum-backed) machines

| # | Machine | Location | States | Notes |
|---|---------|----------|--------|-------|
| B1 | `TaskStatus` | `backend/app/models/task.py:16` | pending → processing → completed / failed | In-memory singleton (`TaskManager`). Volatile: server restart loses every task; `/graph/task/<id>` 404s after reboot. Progress is a server-localized string + int percent. |
| B2 | `ProjectStatus` | `backend/app/models/project.py:17` | created → ontology_generated → graph_building → graph_completed / failed | Persisted JSON. Only linear flow is modeled; `FAILED` is a sink with no recovery transition (reset/delete do it out-of-band). |
| B3 | `SimulationStatus` | `backend/app/services/simulation_manager.py:25` | created → preparing → ready → running → stopping → stopped/completed / failed (+ `paused`) | Persisted `state.json`. Written by **four different callers** (prepare API, start API, stop API, runner sync). `PAUSED` is a dead state — no pause method exists anywhere. |
| B4 | `RunnerStatus` | `backend/app/services/simulation_runner.py:40` | idle → starting → running → stopping → stopped/completed / failed (+ `paused`) | Persisted `run_state.json` + in-memory process/thread/handle maps. This is the *real* lifecycle; B3 is supposed to be its projection via `_sync_simulation_status`, but the API layer also writes B3 directly. Two writers, one truth. |
| B5 | `CommandStatus` | `backend/app/services/simulation_ipc.py:32` | pending → processing → completed / failed | File-based IPC. `PROCESSING` is **never written** — a command jumps from file-exists (implicit pending) to completed/failed. Dead state. |
| B6 | `ReportStatus` | `backend/app/api/report.py` (referenced) | generating → completed / failed | Report dir + `meta.json`; plus a parallel `TaskStatus` task. Two status stores per report, same pattern as B3/B4. |
| B7 | LLM failover chain | `backend/app/utils/llm_client.py:37` | for each entry: attempt → failover (on `_FAILOVER_STATUSES`) → next entry / exhaust | Implicit machine, well-contained. `_LEGACY_ENTRY` sentinel preserves upstream single-provider behavior. Good model — see Part 3. |

### 1B. Backend — implicit (string/bool/derived) machines

| # | Machine | Location | Encoding | Trouble |
|---|---------|----------|----------|---------|
| B8 | Memory batch lifecycle | `services/graph_builder.py:642` (`_wait_for_batch`), `app/memory/graphiti_client.py` | `draft → queued/processing → succeeded/partial/failed/invalid/canceled` | Terminal vocabulary was learned the hard way (600 s timeout, OPENREEF.md §4b). Correct but only documented in OPENREEF.md — not enforceable in code. |
| B9 | Episode `processed` drain | `services/graph_builder.py:721`, `services/zep_graph_memory_updater.py:600` | boolean `processed`, polled 3 s intervals | "pending→False, done→True, failed→terminal True after one retry" is compat-contract knowledge living only in OPENREEF.md. |
| B10 | Platform sub-states | `simulation_runner.py` (`twitter_running`, `twitter_completed`, …), `simulation_manager.py:70` (`twitter_status: str = "not_started"`) | booleans + a **dead string field** | `twitter_status`/`reddit_status` in `SimulationState` are written and never read. Platform "enabled" is re-derived from `actions.jsonl` file existence (`_check_all_platforms_completed`). |
| B11 | Prepare pipeline stages | `simulation_manager.py` (`profiles_generated`, `config_generated`), `api/simulation.py:1259` | bool pair → derived `generation_stage` | The *API layer* derives `generating_profiles/generating_config/completed/failed` from status+flags; the *service layer* has its own stage vocabulary (`reading/generating_profiles/generating_config/copying_scripts`). Two derivations of one pipeline, different names. |
| B12 | "Prepared?" derivation | `api/simulation.py:271` `_check_simulation_prepared` | status whitelist + file existence + `config_generated` | A third definition of readiness. Contains an **auto-heal** that silently rewrites `preparing → ready` in `state.json` — derivation and mutation in one helper. |
| B13 | Build resume/recovery | `api/graph.py:530-575` | `GRAPH_BUILDING` + persisted batch IDs; batch status ∈ {queued, processing, succeeded} ⇒ resume | Good instinct (durable identity, `BatchSubmission`). But recovery logic lives in the API route, not the service; orphan `GRAPH_BUILDING` after restart routes to FAILED-with-`recoverable:true`. |
| B14 | Stop/finalization barrier | `simulation_runner.py` (`_manual_stop_requests`, `SimulationStopPending`), `STOPPING` as ingestion barrier | set membership + exception-as-state | Correct and subtle, but encoded in ~250 lines of lock choreography. No test-visible invariant like "terminal states are only published after drain" — it's only prose comments. |
| B15 | Updater drain protocol | `zep_graph_memory_updater.py:314` | `_running` flag + acceptance lock + worker-drain → flush → fail-check → episode-wait | Failed batches retained **in memory only**; the "durable" claim in comments dies with the process. |
| B16 | Graph readers/writer registry | `utils/zep_lifecycle.py` | per-graph `RLock` + reader set | Process-local, acknowledged in docstring. Fine for single-container; invisible to operators. |
| B17 | Env alive/dead | `env_status.json` via `simulation_ipc.py:270` | `"alive"` / `"stopped"` string | Third persistence mechanism (alongside state.json, run_state.json). Stale `alive` after a crash gates interviews wrongly until timeout. |
| B18 | Graph-memory enablement | `simulation_runner.py` `_graph_memory_enabled` dict | dict get with `False` default | Tri-state truth (enabled/disabled/unknown) collapses to bool; `stop_simulation` recovery depends on it. |

### 1C. Frontend — implicit machines (every one is hand-rolled refs + `setInterval`)

| # | Machine | Location | Encoding | Trouble |
|---|---------|----------|----------|---------|
| F1 | Wizard stepper | `views/MainView.vue:98` | `currentStep` 1–5, event-driven | **Phantom**: MainView only contains Step 1–2 components; steps 3–5 are separate routes (`SimulationRunView`, `ReportView`, `InteractionView`) and each view **hardcodes** its "Step N/5" label. No single source of truth for where the user is. |
| F2 | Project phase | `views/MainView.vue:108` | `currentPhase` −1/0/1/2 | A hand-maintained mirror of `ProjectStatus` with its own switch; drifts if the backend adds a state. |
| F3 | Run view phase + polling | `components/Step3Simulation.vue:320,469` | `phase` 0/1/2 + two timers (2 s status, 3 s detail) + `actionIds` dedup Set + `finalizingLogged` flag | Reasonably resilient (rehydrate, visibility catch-up — the OpenReef step-3 work), but it's ~200 lines of polling choreography that Step2 and Step4 re-implement differently. |
| F4 | Prepare stages | `components/Step2EnvSetup.vue:769,824` | `phase` 0–3 + **three timers** (prepare 2 s, profiles 3 s, config 2 s) | Three overlapping pollers for one pipeline; stage derived from `generation_stage` string. |
| F5 | Report generation | `components/Step4Report.vue:417` | outline → `generatedSections` map → `isComplete` + two log timers | Per-section streaming state kept client-side; refresh mid-generation re-derives from API. |
| F6 | Interaction/chat | `components/Step5Interaction.vue:429` | tab state, `chatHistoryCache` per target, survey flow | Chat history lives only in component memory; navigating away and back loses it. |
| F7 | Upload handoff | `store/pendingUpload.js` | `isPending` + File objects in a reactive store | **Violates fundamental #3**: refresh or deep-link between Home → Process loses the files; there is no recovery — the user starts over. |
| F8 | Home upload form | `views/Home.vue:223` | files/drag/loading/error | Fine, but completion depends on F7's handoff. |
| F9 | History browser | `components/HistoryDatabase.vue` | list + filters | Reads the same simulation JSONs the API enriches differently (`/history` endpoint). Second client of server state. |

### 1D. Dead and duplicated code discovered (DX hazards)

- `views/Process.vue` (2,076 lines) — **imported by nothing**; the router maps `Process` → `MainView.vue`. Two parallel step-1/2 implementations drifting apart.
- `PAUSED` — defined in B3/B4, listed in five "active status" sets, implemented nowhere.
- `CommandStatus.PROCESSING` — never emitted.
- `SimulationState.twitter_status/reddit_status` — written, never read.
- Two step-2 surfaces: `MainView` embedded flow vs standalone `SimulationView` (its own header, own copy of layout state).

---

## Part 2 — Cross-cutting findings

These are the patterns underneath the inventory. Each proposal in Part 3 traces
back to one or more.

1. **Two status stores per entity.** Simulation (B3/B4) and Report (B6/task)
   each keep a metadata status and a runtime status, synced by best-effort
   projection with swallowed errors. Callers must poll both and reconcile
   themselves; the frontend does, in four separate implementations.
2. **State is stringly-typed at every boundary.** APIs serialize enum values to
   bare strings; the frontend switches on literals (`'completed'`, …). Typos
   are runtime-only failures. No shared contract exists between backend and
   frontend.
3. **Locale text is used as machine state.** `task.message` is pre-translated
   server-side and displayed raw; the same message doubles as a log line and a
   progress descriptor. The frontend cannot restyle or re-slice it.
4. **Progress has N incompatible dialects.** Percent ints, stage weights
   (re-invented in prepare API vs build API vs frontend), item counts. None
   carry ETA, stage identity, or terminality.
5. **Polling is the only channel.** No SSE/WS anywhere; 2–3 overlapping timers
   per page; browser throttling of background tabs patched ad-hoc
   (visibility handler) in exactly one of four pollers.
6. **Logs are client-side ephemera.** `systemLogs` arrays live in component
   memory; a refresh wipes the narrative of what happened (backend logs to
   `logs/`, but the UI never reads them).
7. **Recovery is per-feature heroics.** B13 (batch resume) and B14 (stop
   barrier) are excellent but one-off; nothing generalizes them, and only
   graph-build survives a restart.
8. **No transition validation anywhere.** `update_task` and
   `_save_simulation_state` accept any status from any status. Only runner
   `start` checks active statuses. Terminal states can be overwritten by a
   stale caller (e.g. a late prepare failure writing FAILED over COMPLETED is
   possible today via the prepare API's exception path).

---

## Part 3 — Decisions and proposals

Each decision names what wins (UX = user experience, DX = developer
experience, AX = agent experience — both coding agents working this repo and
simulated agents whose world these machines drive) and how it honors the
fundamentals. Effort is small (<2d), medium (2–5d), large (>5d).

### D1. One writer per state store (Single-Writer Lifecycle)

- **Decision**: `run_state.json` becomes the *only* authority for run
  lifecycle. `SimulationState.status` is derived — never written by API code —
  via the existing `_sync_simulation_status` projection, extended to cover the
  full lifecycle (today it maps only 5 of 9 states). Same for reports.
- **Wins**: DX (one place to reason, grep, and breakpoint), AX (an agent can
  trust `run_state.json` unconditionally), UX (status can no longer desync —
  e.g. UI showing "stopped" while runner still drains).
- **Compatibility**: projection failures already degrade to logged errors;
  no API shape changes. Small effort. **First thing to do.**

### D2. `app/openreef/lifecycle.py` — the explicit registry

- **Decision**: one OpenReef-namespaced module holding every lifecycle enum,
  its transition table, terminal sets, and a `validate_transition()` helper.
  Retrofit behind a warn-only flag (`REEF_STRICT_STATE=1` upgrades warnings to
  errors for CI); default behavior unchanged.
- **Contents**: `Lifecycle` for B1–B6; `BatchLifecycle` for B8 (terminal set
  `succeeded/partial/failed/invalid/canceled` — currently a bare set literal
  in `graph_builder.py:642`); `DrainPhase` for B9/B15; documented dead-state
  list.
- **Wins**: DX/AX — an agent doing `grep -r "Lifecycle" app/openreef` finds the
  whole map; transition bugs become testable invariants
  (`test_lifecycle_contract.py` in the spirit of `test_memory_contract.py`).
- **Compatibility**: additive module + labeled hook lines only. Small.

### D3. Bury the dead states

- **Decision**: mark `PAUSED` (B3/B4), `CommandStatus.PROCESSING` (B5), and
  `SimulationState.twitter_status/reddit_status` (B10) `@deprecated` with a
  pointer to this file; remove them from active-status sets (they are
  unreachable, so removal is behavior-neutral); schedule physical deletion for
  the next major.
- **Wins**: DX/AX — status sets stop lying about what the system can do; new
  contributors don't design against features that don't exist.
- **Compatibility**: enum *values* stay parseable (old JSON loads fine). Small.

### D4. Typed status envelope at the API edge

- **Decision**: add an additive `status_v2` block to `/run-status`, `/task`,
  and prepare/status responses:
  `{"machine": "runner", "state": "running", "terminal": false, "since": <iso>,
  "reason": null, "progress": {"stage": "simulating", "percent": 12.5,
  "items": {"current": 18, "total": 144}}, "next_states": ["stopping", "completed", "failed"]}`.
  Message localization moves client-side via `message_key` + args (old
  `message` string kept for upstream parity).
- **Wins**: UX (frontend renders consistent progress + honest next-states;
  locale leakage fixed), DX (one TS-shaped contract; typos caught at build
  time once the frontend types it), AX (machines reading the API get semantics
  without scraping prose).
- **Compatibility**: purely additive JSON. Medium.

### D5. One event stream, opt-in (`REEF_EVENTS=1`)

- **Decision**: server-sent events endpoint per project/simulation publishing
  lifecycle transitions, progress ticks, and action batches. Frontend gains an
  event-driven store; **polling remains the default** (fundamental #1 — SSE is
  inert without the flag).
- **Wins**: UX (no more 2–3 overlapping timers, no background-tab throttling
  bug class, instant state transitions), DX (delete ~500 lines of poller
  choreography), and it **is the substrate for ReefLive** (OPENREEF.md feature
  #6 — "watch agents work" becomes a subscription, not a new polling system).
- **Compatibility**: new route + flag; no existing route changes. Large, so
  phase it: backend stream first, one view migrated, then the rest.

### D6. Server-persisted journal logs

- **Decision**: backend appends human-readable log lines to a rolling JSONL
  per project and per simulation (`uploads/journals/`); UI rehydrates on
  mount. Replaces the four hand-rolled `systemLogs` arrays.
- **Wins**: UX — closes the last refresh-loses-context gap (fundamental #3's
  stated intent); the simulation narrative survives a closed tab **and** a
  server restart; support requests become "paste the journal."
- **Compatibility**: additive files; UI falls back to empty journal. Small-medium.

### D7. Durable tasks + orphan sweep

- **Decision**: `TaskManager` journals tasks to `uploads/tasks/<id>.json`
  (append-only status log per task); on boot, mark `PROCESSING` orphans
  `FAILED(reason="server_restart")`. Graph build keeps its batch-level resume
  (B13); prepare gains artifact-level resume (D8).
- **Wins**: UX (a restart no longer turns "where's my build?" into a 404),
  DX (task state inspectable on disk, greppable). Small.
- **Note**: satisfies the single-container rule; no service added.

### D8. Idempotent prepare

- **Decision**: key prepare work by `(simulation_id, entity_types, use_llm,
  web_search_enabled, requirement+config hash)`; when artifacts exist and the
  key matches, skip completed stages (profiles file ⇒ skip stage 2; config
  file ⇒ skip stage 3). `force_regenerate=true` bypasses, as today.
- **Wins**: UX (refreshing during prepare no longer restarts profile
  generation from zero — today the realtime-written profiles file is ignored
  on re-prepare), AX (prepare becomes safe to retry from scripts/agents).
- **Compatibility**: same API; only the skip logic is new. Medium.

### D9. Kill the upload handoff hole

- **Decision**: Home submits immediately — call `generateOntology` on the Home
  page itself, then route to `/process/<project_id>`; `pendingUpload` store is
  deleted. No window exists where files live only in component memory.
- **Wins**: UX (fundamental #3 honored on the first screen; back/forward/refresh
  all safe), DX (one store and one flow deleted). Small.

### D10. Frontend state ownership: composables, not components

- **Decision**: introduce `useProjectState(id)`, `useSimulationState(id)`,
  `useRunState(id)` composables ( Pinia or plain module stores) that own
  polling/subscription, rehydration, and log hydration; components subscribe.
  Delete per-component timer choreography (F3–F5).
- **Wins**: DX (one implementation of "follow a run"), UX (consistent
  behavior across views; F6 chat history persists by keeping the store outside
  the component). Medium.

### D11. Quarantine dead views

- **Decision**: move `views/Process.vue` and `views/SimulationView.vue`'s
  orphaned duplication into `frontend/src/legacy/` with a README, out of the
  build; route "Step N/5" labels to route meta (`{name:'SimulationRun', meta:{step:3}}`)
  so the wizard chrome has one source.
- **Wins**: DX/AX — agents and humans stop reading 2,500 lines of dead code;
  the wizard model becomes data instead of hardcoded strings. Small.

### D12. i18n hardening

- **Decision**: no user-facing literal outside `locales/*.json`; extend the
  existing locale-parity CI with a frontend source scan (grep for
  `>[A-Z][a-z]+ [a-z]+<` heuristics + a denylist file). Today Step3 ships
  hardcoded English ("Info Plaza", "Waiting for agent actions…", "Available
  Actions") in an app that otherwise i18ns.
- **Wins**: UX (zh users stop seeing mixed-language chrome), DX (locale test
  catches it). Small.

---

## Part 4 — Design & UX philosophy: the Reef Principles

We are still wearing MiroFish's skin: the fish logo, the "MIROFISH" wordmark
in the header of every view, hardcoded English chrome, and a dev-console
aesthetic (dense mono fonts, raw log consoles as the primary feedback
surface). OpenReef needs its own philosophy, consistent with its mission
("run the whole engine on your own infrastructure" — your reef, your ocean)
and with the fork's product shape (a *living* simulation you observe and
steer, not a CI dashboard you babysit).

### The principles

1. **The Reef is alive.** Running work is ambient and calm — gentle motion,
   breathing status, no alarm-red for routine states. Completion is a
   settling, not a siren. Long-running simulations are *watched*, so the
   default look is dark, low-glare, and legible for hours.
2. **State is the interface.** The lifecycle *is* the navigation. Every screen
   answers three questions without scrolling: Where am I? What's happening
   right now? What's next (and what's the escape hatch)? Dead-end screens
   (blank panels, silent failures) are bugs.
3. **Depth over clutter.** Progressive disclosure: a calm summary layer
   (cards, progress, next action) with logs and raw JSON available one level
   down. Logs are scuba gear — carried, not stared at.
4. **One ocean, two currents.** Twitter/Reddit (Info Plaza / Topic Community)
   are two currents in *one* reef, not two dashboards. One timeline, one
   progress, color-split by platform; the split-view is a lens, not a fork.
5. **Speak human, render machine.** Every chrome string localized (D12);
   every status surface carries a machine-readable sibling (`data-status`,
   `aria-live`) so assistive tech and automated agents read the same truth.
6. **Local-first sovereignty.** Copy and affordances reflect that the reef
   lives on the user's machine: durability is visible (journals, export),
   nothing requires a vendor cloud to explain itself.
7. **Calm by default, detail on demand; motion respects
   `prefers-reduced-motion`.** Motion budget: only state changes animate.
8. **Identity: OpenReef, not MiroFish.** UI chrome (header, tab title,
   favicon, loading screens, error pages) carries OpenReef branding and the
   reef visual identity. MiroFish attribution stays in README/OPENREEF.md —
   gratitude is documentation, not chrome.

### Visual identity directions (pick one in design phase)

- **Palette**: abyss-to-reef gradient blues as base; coral/amber as the single
  warm accent (running state); bioluminescent cyan for agent activity; green
  reserved for terminal-success. Platform split: two distinguishable,
  colorblind-safe hues.
- **Type**: keep the mono accent for IDs/numbers (it suits the machine
  surface) but move chrome text to a neutral grotesque; CJK parity checked.
- **Logo**: wordmark "OpenReef" + reef mark (abstract coral/branch), delivered
  as SVG + favicon set, dark/light variants.
- **Tokens**: single `tokens.css` (color, spacing, radius, elevation, motion)
  consumed by all components; dark mode is the default theme, light supported.

---

## Part 5 — The UX rework roadmap (phased)

Ordered so each phase is shippable, tested, and merge-boring. Every phase ends
with `uv run pytest backend/tests -q`, `npm run build`, locale parity green.

### Phase R0 — Hygiene & honesty (no visual redesign yet)

1. D1 single-writer lifecycle + D3 bury dead states (backend).
2. D11 quarantine dead views; route-meta step labels.
3. D9 upload handoff fix.
4. D12 i18n hardening sweep (Step3 hardcoded strings first).
5. Brand separation: OpenReef wordmark/favicon/tab title; MiroFish logo
   removed from app chrome (kept in README).
- **Outcome**: nothing looks different except correct labels, correct status,
  no dead ends. All backend changes covered by new
  `test_lifecycle_contract.py`.

### Phase R1 — Foundations

6. D2 lifecycle registry + D4 status envelope (backend + frontend types).
7. D7 durable tasks; D8 idempotent prepare.
8. D6 journal logs; UI log panels hydrate from journal.
9. D10 composable stores; delete duplicated pollers.
10. `tokens.css` extracted from existing styles; dark theme tokens defined
    (not yet switched on).
- **Outcome**: the whole app runs on explicit machines; refresh loses nothing
  anywhere; the frontend has typed state.

### Phase R2 — Experience

11. D5 SSE event stream (opt-in) + migrate run view; **ReefLive watch view**
    (OPENREEF.md feature #6) built on the stream: unified dual-current
    timeline with agent-level drill-down.
12. Visual identity rollout: palette, type, logo, dark-first theme;
    calm-motion pass; reduced-motion support.
13. Unified run view merges `SimulationRunView` + Step3 surfaces; wizard
    chrome becomes a real stepper driven by route meta + server state.
- **Outcome**: OpenReef looks and feels like itself; watching agents work is
    a first-class, subscribable experience.

### Phase R3 — Depth (ties into pending features)

14. ReefReport formats (feature #5) styled under the new identity
    (HTML/PDF/infographics consume tokens).
15. Report customization presets (feature #4) — UI over the D4 envelope.
16. Agent-facing surfaces: per-agent profile cards, interview transcripts from
    journals (AX for the *simulated* agents: their world is inspectable).

---

## Part 6 — Verification & safety commitments

- Every phase: backend pytest green (fork CI), frontend build green, locale
  parity green (existing CI catches it), `verify_reef_memory.py` unaffected.
- New lifecycle invariants get real-component tests in the spirit of
  `verify_reef_memory.py` (no assumption-shaped fakes): a lifecycle verifier
  that drives SimulationRunner start→stop→drain against the real updater and
  asserts terminal-state-only-after-drain.
- OPENREEF.md §3/§4 inventory updated in the same commit as any file it
  describes; this ROADMAP gets status checkboxes as items land.
- All new capabilities opt-in or additive; no default behavior changes; one
  container unchanged; upstream files get only labeled hook lines.

---

## Appendix — quick reference: where each machine lives

```
Backend
  TaskStatus/ProjectStatus/SimulationStatus/RunnerStatus/CommandStatus
    → app/models/task.py, app/models/project.py,
      app/services/simulation_manager.py, app/services/simulation_runner.py,
      app/services/simulation_ipc.py
  Batch + episode drain → app/services/graph_builder.py,
      app/services/zep_graph_memory_updater.py, app/memory/graphiti_client.py
  Prepared-derivation + resume → app/api/simulation.py, app/api/graph.py
  Stop barrier + manual-stop → app/services/simulation_runner.py
  Readers/writer registry → app/utils/zep_lifecycle.py
  Failover chain → app/utils/llm_client.py (+ llm_providers.py)

Frontend
  Stepper/phase → views/MainView.vue, views/*View.vue, router/index.js
  Run polling → components/Step3Simulation.vue
  Prepare polling → components/Step2EnvSetup.vue
  Report streaming → components/Step4Report.vue
  Chat/interview → components/Step5Interaction.vue
  Upload handoff → store/pendingUpload.js, views/Home.vue
```
