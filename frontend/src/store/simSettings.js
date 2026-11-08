/**
 * OpenReef simulation settings — runtime-independent options collected on
 * Step 1 (advanced section) and consumed later in the flow:
 *   - Step 2: prepare payload (web search), rounds prefill
 *   - Step 3: report generation payload (report options)
 *
 * Persisted in sessionStorage so a page refresh keeps the user's choices.
 * Everything is optional; defaults produce upstream behavior.
 */
import { reactive } from 'vue'

const STORAGE_KEY = 'openreef.simSettings.v1'

const defaults = () => ({
  reportOpts: {
    preset: 'standard',
    language: '',
    length: '',
    tone: '',
    complexity: '',
    audience: ''
  },
  webSearch: false,
  maxRounds: null
})

function load() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return defaults()
    const parsed = JSON.parse(raw)
    return { ...defaults(), ...parsed, reportOpts: { ...defaults().reportOpts, ...(parsed.reportOpts || {}) } }
  } catch {
    return defaults()
  }
}

const state = reactive(load())

function persist() {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch {
    /* storage unavailable (private mode) — session-only is fine */
  }
}

export function getSimSettings() {
  return state
}

export function updateSimSettings(partial) {
  Object.assign(state, partial)
  persist()
}

export function updateReportOpts(partial) {
  Object.assign(state.reportOpts, partial)
  persist()
}

/** Payload for POST /api/report/generate (omits empty = default choices). */
export function buildReportOptionsPayload() {
  const o = state.reportOpts
  const payload = { preset: o.preset }
  if (o.language) payload.language = o.language
  if (o.length) payload.length = o.length
  if (o.tone) payload.tone = o.tone
  if (o.complexity) payload.complexity = o.complexity
  if (o.audience && o.audience.trim()) payload.audience = o.audience.trim()
  return payload
}

export function resetSimSettings() {
  Object.assign(state, defaults())
  persist()
}
