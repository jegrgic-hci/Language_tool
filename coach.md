# Coaching System — Reference

## Overview

The coaching system analyses a user's word-level accuracy across all shadowing attempts and surfaces targeted insights. It is backed by `/coach?access_code=` (FastAPI) → `get_coach_data()` in `analytics.py`, rendered in the in-app coach panel (`static/index.html`).

Results are cached per access code and recomputed after every 20 new events (`stale_after=20` in `get_cached_coach()`). To force a refresh, hit `POST /coach/refresh?access_code=` or clear the `coach_cache` table directly.

---

## Data pipeline

```
phrase_attempted / chunk_attempted / sentence_drilled events
  → get_word_accuracy(access_code, min_attempts=3)
      → per-word: attempts, accuracy, error_type, top_substitutions
  → get_coach_data()
      → percentile thresholds
      → coaching buckets
      → cached in coach_cache table
  → /coach endpoint
  → frontend coach panel
```

---

## Attempt-count percentile thresholds

Computed inside `get_coach_data()` from the full `word_acc` list:

| Variable   | Percentile | Used by                          |
|------------|-----------|----------------------------------|
| `high_att` | p75       | almost_there, inconsistent, tech_suspect |
| `mid_att`  | p50       | worst_words (WORK ON THIS)       |
| `low_att`  | p25       | quick_pickup (QUICK WINS)        |

---

## Coaching buckets

Sections appear in this order in both the frontend coach view (`static/index.html` → `renderCoachData()`) and the analytics dashboard (`server.py` → `render_coach_tab()`). Both views are kept in sync — same thresholds, same copy, same section order.

### MASTERED (hero block)
- **Source**: `mastered_words`
- **Accuracy threshold**: `>= 90%`
- **Sort**: most-attempted first
- **Intent**: large number displayed at the top of the panel; word chips shown inline

### WORK ON THIS
- **Source**: `worst_words` (frontend filters at render time)
- **Attempts gate**: `>= mid_att` (p50)
- **Accuracy threshold**: `< 30%`
- **Intent**: words practiced frequently and consistently wrong — biggest opportunity
- **Button**: starts a coach focus session with these words in shadow mode

### CHECK YOUR MIC
- **Source**: `tech_suspect_words`
- **Attempts gate**: `>= high_att` (p75)
- **Accuracy threshold**: exactly `0.0`
- **Intent**: words never recognised across many attempts — likely mic/STT issue, not pronunciation

### NOTICE THIS
- **Source**: `error_clusters["acoustic_miss"]` only
- **Accuracy threshold**: exactly `0.0` — mic has never picked up the word
- **Sort**: most-attempted first (set in `analytics.py`)
- **Display cap**: 10 words
- **Shown when**: ≥ 2 qualifying words
- **Intent**: mic consistently fails to catch these — needs more energy on the first consonant
- **Button**: starts a coach focus session with these words in shadow mode

### PATTERNS
- **Source**: `error_clusters["homophone"]` then `error_clusters["substitution"]` — one card each if qualifying
- **Accuracy threshold**: `< 50%` — wrong more often than not
- **Sort**: `attempts × (1 − accuracy)` descending (set in `analytics.py`)
- **Display cap**: 10 words per card
- **Shown when**: ≥ 2 qualifying words in the cluster
- **Intent**: surface recurring substitution patterns — homophones (precision issue) vs. wrong-word replacements (needs isolation practice)
- **Button**: starts a coach focus session with those words in shadow mode

### INCONSISTENT
- **Source**: `inconsistent_words`
- **Attempts gate**: `>= high_att` (p75)
- **Accuracy band**: 50–70%
- **Intent**: words that get through sometimes but haven't locked in
- **Button**: starts a coach focus session with these words in shadow mode

### ALMOST THERE
- **Source**: `almost_there_words`
- **Attempts gate**: `>= high_att` (p75)
- **Accuracy band**: 70–90%
- **Intent**: close to mastery — a few more clean reps

### QUICK WINS
- **Source**: `quick_pickup_words`
- **Attempts gate**: `<= low_att` (p25)
- **Accuracy threshold**: `>= 85%`
- **Intent**: nailed quickly with fewer attempts than average — natural strengths

---

## Error type classification (`_classify_substitution` in analytics.py)

Each missed word is classified into one of four types:

| Type              | Condition                                              |
|-------------------|--------------------------------------------------------|
| `acoustic_miss`   | `said` is empty — mic didn't pick up the word at all   |
| `homophone`       | `said` maps to target via `FRENCH_HOMOPHONES` table    |
| `elision_variant` | normalised forms match after elision contraction       |
| `substitution`    | none of the above — completely different word          |

---

## Session tracking

As of May 2026, sessions use a two-ID system:

- **`session_id`**: persistent UUID stored in `localStorage` — identifies the user/browser permanently
- **`visit_id`**: new UUID generated on every page load — brackets one actual usage session

`session_start` fires on access code validation (page load or first entry).
`session_end` fires via `navigator.sendBeacon` on `visibilitychange`/`beforeunload`, with `duration_seconds` payload.
Inactivity timeout: 20 minutes of no interaction fires `session_end`; next interaction starts a fresh `visit_id`.

Analytics session count = distinct `visit_id` values in `session_start` events (falls back to `session_id` for pre-update rows).

---

## Pending work / known issues

- **Pattern clustering for WORK ON THIS**: 200-word lists are unwieldy. Planned approach: group struggling words by phonetic pattern (nasal vowels, uvular R, silent letters, etc.) and surface the pattern rather than individual words. Hybrid rule-based + Mistral fallback.
- **Per-session coaching**: `get_session_history()` now returns per-visit summaries — the coach panel does not yet use this to weight recent sessions more heavily than historical ones
