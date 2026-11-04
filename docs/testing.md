# Testing OpenReef in containers

Everything below runs on this Mac with Apple containers (macOS 26+). The
same image builds and runs under Docker — the Dockerfile is unchanged from
upstream.

## 1. Run the fork

```bash
cd ~/Documents/projects/mirofish-projects

# Fork (what we've been building):
MIROFISH_SRC=$HOME/Documents/projects/OpenReef ./containers/run-mirofish.sh

# Back to pristine upstream any time:
./containers/run-mirofish.sh            # default SRC_DIR is ~/MiroFish
```

Notes:

- The script rebuilds `mirofish:local` from `MIROFISH_SRC` on every start
  and recreates the container, so `.env` changes apply on restart.
- `.env` pointing at a **hosted** API (e.g. Cloudflare)? Add `MIROFISH_SHIM=0`:
  the LLM shim is only for local oMLX (it caps tokens and injects a
  non-standard `repetition_penalty` some hosted APIs reject).
- Shim path (local oMLX): keep the default; the shim starts automatically.
- UI: http://127.0.0.1:3000 · Backend: http://127.0.0.1:5001
- Logs: `./containers/run-mirofish.sh logs`
- Sim logs on the host: `~/mirofish-data/simulations/<simulation_id>/simulation.log`
- Full reset: `./containers/run-mirofish.sh wipe`

## 2. Test matrix

### Regression first — no new env vars

With your current `.env` (no `LLM_PROVIDERS_JSON`, no `TAVILY_API_KEY`),
run the standard flow end-to-end with a short sim: upload
`scenarios/crypto-confidence-collapse/scenario.md` as the seed doc, paste
`prompt.md` as the requirement, set **custom rounds = 10** in Step 2.
Everything must behave exactly like upstream MiroFish.

### Feature 3 — English

1. Open the UI in a **private window** (no saved locale) → the UI should
   appear in your browser's language (English → English).
2. Or flip the language switcher manually.
3. Run a short sim to report → the report (title, summary, sections,
   quotes) must be fully English. Switch to 中文 and confirm the report
   generates in Chinese as before.

### Feature 1 — multi-LLM

1. **No registry:** `curl http://127.0.0.1:5001/api/llm/providers` →
   `"active": false`. Step-2 panel shows "Single provider".
2. **With registry:** create `llm.config.json` next to your `.env` (copy
   `llm.config.example.json` from the fork as a starting point), restart —
   the run script mounts it automatically:

   ```json
   {
     "providers": [
       {"name":"primary","base_url":"https://api.openai.com/v1","api_key":"${OPENAI_API_KEY}","model":"gpt-4.1-mini"},
       {"name":"local","base_url":"http://host.container.internal:8001/v1","api_key":"root","model":"Qwen3.5-4B-MLX-4bit"}
     ],
     "chains":{"default":["primary"],"simulation":["local"]}
   }
   ```

   Panel now lists providers + chains; the **Test** button pings any
   provider and shows latency. (Advanced: `LLM_PROVIDERS_FILE` /
   `LLM_PROVIDERS_JSON` env vars still work — see docs/multi-llm.md.)
3. **Failover:** point the default chain's first provider at a dead URL
   (`http://127.0.0.1:9/v1`) with a real second entry → build a graph →
   backend logs show `failing over to next provider` and the step still
   succeeds.
4. **Stage routing:** with a `"simulation"` chain entry (example above),
   start a sim and check `simulation.log`: the `LLM配置: model=…` line must
   show the local model, while ontology/report still use the default chain.

### Feature 2 — web search

1. **No key:** enable the Step-2 toggle anyway → sim runs normally;
   `simulation.log` shows
   `[web_search] config enabled but TAVILY_API_KEY is not set; skipping`.
2. **With key:** add `TAVILY_API_KEY=tvly-...` to `.env`, restart, enable
   the toggle → `simulation.log` shows
   `[web_search] Tavily search attached to N agents`. (Watch agent behavior
   in the logs; Tavily free tier = 1000 searches/mo.)
3. Config check after prepare:
   `cat ~/mirofish-data/simulations/<id>/simulation_config.json | grep -A1 web_search`
   → `"enabled": true`.
4. Report agent: with search enabled, the report's tool list includes
   `web_search` (visible in the backend logs during report generation).

## 3. Automated checks already running

- `backend/tests` pytest (188 tests) runs on every push via
  `.github/workflows/test.yml`.
- Frontend `npm run build` verified locally.
