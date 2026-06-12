# Analytics System

vraifrench teacher analytics — event tracking, coach data, and the teacher dashboard.

---

## Overview

SQLite event log (`data/analytics.db`) records student practice activity in real time. The teacher dashboard (`/analytics/dashboard`) reads from the same database via JSON API endpoints. There is no server-side rendering — `static/analytics.html` is a static file that fetches all data after page load.

---

## Database

**Location:** `data/analytics.db` — overridable via `DATA_DIR` env var.

### Tables

#### `events`
Append-only log. Every practice action writes a row here.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `session_id` | TEXT | Browser session UUID |
| `access_code` | TEXT | Student identifier |
| `event_type` | TEXT | See event taxonomy below |
| `payload` | TEXT | JSON — event-specific fields |
| `ts` | DATETIME | UTC (`datetime('now')`) |
| `visit_id` | TEXT | Legacy tab ID — no longer used for session counting (see Session tracking below) |

Indexes: `idx_code` on `access_code`, `idx_type` on `event_type`.

#### `students`

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | |
| `access_code` | TEXT UNIQUE | Login code — auto-generated 6-char alphanumeric |
| `teacher_id` | INTEGER | FK → teachers.id (nullable) |
| `name` | TEXT | Display name |
| `email` | TEXT | Optional |
| `lesson_days` | TEXT | JSON array of 3-letter abbreviations e.g. `["Mon","Wed"]` |
| `lesson_time` | TEXT | Free text e.g. `"10:00"` |
| `notes` | TEXT | Teacher notes |

#### `coach_cache`
Last computed coaching payload per student. Invalidated after 20 new events.

| Column | Description |
|---|---|
| `access_code` | PK |
| `payload` | JSON — full `get_coach_data()` result |
| `events_at` | Event count at cache write time |

#### `teachers`
Minimal table for future multi-teacher support.

| Column | Description |
|---|---|
| `key` | UNIQUE — the analytics API key for this teacher |
| `name`, `email` | Display info |

---

## Event Taxonomy

Events are written by `_analytics.track()`, called from routes in `server.py`.

| Event | When fired | Key payload fields |
|---|---|---|
| `session_start` | Browser tab opens | `visit_id` |
| `session_end` | Tab closes / unload | `duration_seconds` |
| `shadowing_time` | Student exits a view | `view`, `duration_seconds` |
| `chunk_listened` | Paragraph chunk audio replayed | `paragraph_id`, `chunk_index`, `chunk_size` |
| `phrase_attempted` | Phrase shadow submitted | `exercise_type`, `level`, `topic`, `score`, `passed`, `attempt_number`, `word_results` |
| `paragraph_started` | Paragraph exercise opened | `exercise_type`, `paragraph_id`, `level`, `topic`, `sentence_count` |
| `paragraph_attempted` | Paragraph chunk shadowed | `exercise_type`, `paragraph_id`, `chunk_index`, `level`, `score`, `attempt_number`, `word_results` |
| `paragraph_drilled` | Single sentence drilled | `exercise_type`, `paragraph_id`, `chunk_index`, `sentence_index`, `level`, `score`, `attempt_number`, `word_results` |
| `word_attempted` | Word drill / check | `exercise_type`, `mode`, `source`, `word`, `level`, `score`, `attempts` |
| `text_revealed` | Student reveals blurred text (listening comprehension signal) | `context`, `paragraph_id`, `chunk_index`, `listens_before_reveal` |

**`word_results` format:** `[word, matched]` (legacy) or `[word, matched, said]` (current).

**`stt_confidence`** (added to `phrase_attempted`, `paragraph_attempted`, `paragraph_drilled`): Web Speech API confidence for the attempt, `0.0–1.0` or `null`. Sourced client-side and forwarded via the `confidence` request field. Chrome's `fr-FR` confidence is unreliable — treat as a coarse 3-band signal (low <0.65 · mid 0.65–0.85 · high ≥0.85), matching the frontend warning cutoffs. Used to disambiguate `acoustic_miss`: empty-`said` + high confidence ≈ genuine production failure (teach it); empty-`said` + low confidence ≈ STT noise (de-noises `tech_suspect_words`).

**`text_revealed`** — the only direct listening-comprehension signal. Fires when a student un-blurs text:
- `context`: `"chat"` (listening-mode bubble reveal — soft signal, no correctness check) · `"phrase"` (phrase-view blur toggle off) · `"paragraph"` (paragraph blur toggle off)
- `listens_before_reveal` (paragraph only): listens incl. replays for the current chunk at reveal time. Trending *down* over weeks = the student's ear is improving. A paragraph chunk *passed in listening mode with no `text_revealed`* = demonstrated comprehension.
- Not yet added to `_SESSION_EVENTS` — a pure-listening chat session (reveals only, no attempts) won't extend a gap-based session. Revisit if chat listening mode sees heavy use.

**`word_attempted` modes:**
- `mode: "check"` — quick timed word check (fired via sendBeacon)
- `mode: "drill"` — 10× drill with Mistral analysis

**`word_attempted` sources:**
- `"phrase_exercise"` — inline word drill triggered from phrase feedback
- `"paragraph_drill"` — word check in sentence drill feedback
- `"practice_list"` — Practice List word card

**`paragraph_attempted` vs `paragraph_drilled`:** `attempted` = student shadows a full multi-sentence chunk. `drilled` = student isolates a single struggling sentence.

**`passed` on `phrase_attempted`:** stored explicitly (not derived), so it reflects the threshold the student actually experienced. Current pass threshold: score ≥ 0.90.

**DB migrations run at startup** via `init_db()`: `chunk_attempted` → `paragraph_attempted`, `sentence_drilled` → `paragraph_drilled`.

**Pass thresholds:** paragraph chunk ≥ 0.70 · phrase ≥ 0.90.

---

## Session Tracking

**`visit_id` is not used for session counting.** It marked one browser tab open — a student returning to the same tab hours later still counted as one session.

**Current approach: activity-gap sessions.** A new session begins whenever the gap between consecutive practice events exceeds `SESSION_GAP_MINUTES = 20`. Computed in Python at query time from event timestamps; no schema changes needed; works retroactively.

Events counted as active for gap splitting: `session_start`, `paragraph_started`, `chunk_listened`, `phrase_attempted`, `paragraph_attempted`, `paragraph_drilled`, `word_attempted`. `session_end` is excluded (fires on tab close, long after practice ends).

Session duration = span from first to last event in the session group.

---

## API Endpoints

All teacher-facing endpoints require `?key=<ANALYTICS_KEY>`. Set `ANALYTICS_KEY` in `.env` — comma-separated for multiple keys. Returns 403 if missing or invalid.

| Method | Path | Description |
|---|---|---|
| `GET` | `/analytics/dashboard?key=` | Serves `static/analytics.html` |
| `GET` | `/analytics?key=` | Aggregate stats for all students → `{ code: {...} }` |
| `GET` | `/analytics/students?key=` | Roster data for all students → `{ students: [...] }` — used by dashboard init |
| `GET` | `/analytics/sessions?key=&access_code=` | Per-session history for one student (last 20) |
| `GET` | `/analytics/word-accuracy/download?key=&access_code=` | CSV export of word accuracy |
| `POST` | `/analytics/reset?key=&access_code=` | Delete all events + coach cache for a student |
| `POST` | `/analytics/students?key=` | Create student (body: `AddStudentRequest`) |
| `PUT` | `/analytics/students/{access_code}?key=` | Update student profile fields |
| `GET` | `/coach?access_code=` | Coaching summary — returns cache if fresh, recomputes if stale |
| `POST` | `/coach/refresh?access_code=` | Force-recompute and recache coaching data |
| `GET` | `/dashboard` | Redirect → `/analytics/dashboard?key=<first key>` |

**Note:** `/coach` is not key-protected — any caller with a valid `access_code` can read it.

**Progress endpoint (powers the student Home landing AND the teacher Progress tab — LIVE):**

| Method | Path | analytics.py function |
|---|---|---|
| `GET` | `/analytics/progress?access_code=` | `get_home_data()` |

Access-code only (no teacher key), mirroring `/coach` — the student tool has no analytics key.
Returns the Home payload `{ week_labels, kpis, default_key, signals }` (see the **Student Home
page** section below for the full shape). Consumed by `loadHomeView()` in `static/index.html`
**and** by `renderProgress()` in `static/analytics.html` — the teacher Progress tab mirrors the
student view by calling this same endpoint (the teacher page already holds the access code).

> The earlier `{ trend, mastery, summary }` shape (`get_progress_trend` / `get_word_mastery_trend`
> / `get_progress_summary`) is superseded by `get_home_data`; those three functions remain in
> `analytics.py` but are no longer wired to an endpoint.

**Per-student detail endpoints (live — added in the 4-tab rebuild):**

| Method | Path | analytics.py function |
|---|---|---|
| `GET` | `/analytics/trend?key=&access_code=` | `get_score_trend()` — **no longer called by the dashboard** (Progress tab now uses `/analytics/progress`); endpoint retained |
| `GET` | `/analytics/practice?key=&access_code=&window=since\|30d\|all` | `get_practice_since()` (+ top topics) — **no longer called by the dashboard** (the KPI row it fed was replaced by the tutor-insights strip); endpoint retained |
| `GET` | `/analytics/paragraph?key=&access_code=&window=since\|30d\|all` | `get_paragraph_exercise_stats()` |
| `GET` | `/analytics/phrase?key=&access_code=&window=since\|30d\|all` | `get_phrase_exercise_stats()` |
| `GET` | `/analytics/words?key=&access_code=` | `get_word_accuracy()` |
| `GET` | `/analytics/content?key=&access_code=` | `get_topic_coverage()` + `get_listen_speak_ratio()` |

`window` is mapped to `since_days` by `_window_to_since_days()` in `server.py`: `30d`→30, `all`→None, `since`→days since last lesson (falls back to 30 if no schedule).

---

## `analytics.py` — Function Reference

### Student management

| Function | Description |
|---|---|
| `init_db()` | Create tables + run migrations. Called at server startup. |
| `track(session_id, access_code, event_type, payload, visit_id)` | Append an event row |
| `add_student(name, email, lesson_days, lesson_time, notes)` | Auto-generates a 6-char access code |
| `update_student(access_code, ...)` | Partial update — only non-None fields written |
| `get_all_students()` | All student rows as list of dicts |
| `get_student_by_code(access_code)` | Single student lookup |
| `last_lesson_date(lesson_days_json)` | Most recent completed lesson day — never today, walks back up to 8 days |
| `next_lesson_date(lesson_days_json)` | Next upcoming lesson day — includes today, looks forward up to 8 days |
| `get_practice_since(access_code, since: date)` | Sessions, days active, attempts, avg score, struggles, total/avg duration — since a given date |
| `get_roster()` | All students with roster card stats; sorted by `days_until_next` asc, no-schedule students last |

### Aggregate queries

| Function | Returns |
|---|---|
| `get_analytics()` | `{code: _aggregate(rows)}` for every code with events |
| `get_session_history(access_code, limit=20)` | Per-session summaries via gap analysis — attempts, scores, new/revisited words, duration |
| `get_word_accuracy(access_code, min_attempts=5)` | Per-word accuracy + top substitutions + error type classification |
| `get_paragraph_exercise_stats(access_code, since_days=None)` | Started / completed / phrases drilled / words practiced — overall + by level |
| `get_phrase_exercise_stats(access_code, since_days=None)` | Started / completed / stuck / avg attempts to complete — overall + by level |
| `get_topic_coverage(access_code)` | Attempts + avg score per topic, sorted by attempts desc |
| `get_listen_speak_ratio(access_code)` | Listens vs speak attempts + ratio + avg replays per chunk |
| `get_score_trend(access_code, weeks=8)` | Weekly score buckets (8 weeks, incl. empty) + recent/lifetime avgs + delta (last 30d vs prior 30d) |
| `get_progress_trend(access_code, weeks=8)` | Weekly score+pass series split by exercise type (overall/paragraph/phrase/word) **and** CEFR level, null-padded per week. Powers the student Home landing chart (DV3 level-split). Pass thresholds: paragraph 0.70, phrase/word 0.90 |
| `get_word_mastery_trend(access_code, weeks=8)` | Cumulative words-mastered curve, latched (never decreases). A word masters at ≥3 attempts & ≥80% hit-rate. Also returns `total_mastered`, `newly_mastered` (last week), `mastered_30d` |
| `get_progress_summary(access_code)` | The three Home-landing KPI cards: words mastered (+30d gain), within-level accuracy trend, avg session length (+30d change). See `/analytics/progress` |
| `get_score_trajectories(access_code)` | Per-item mastered / improving / plateaued / stuck counts |
| `get_sentence_drill_breakdown(access_code)` | Drill attempts + scores by difficulty level |

### Session helpers

| Function | Description |
|---|---|
| `_split_session_groups(ts_list)` | Splits sorted timestamp list into `(start_ts, end_ts)` pairs using `SESSION_GAP_MINUTES` |
| `_group_events_into_sessions(rows)` | Groups `(ts, event_type, payload)` rows into session buckets |

### Coach system

| Function | Description |
|---|---|
| `get_coach_data(access_code)` | Full coaching summary — all deterministic, no LLM |
| `get_cached_coach(access_code, stale_after=20)` | Returns cache if event count grew < 20 since write, else None |
| `set_cached_coach(access_code, payload)` | Write/overwrite cache entry |

**Word error classification** (`_classify_substitution`):
- `homophone` — said word maps to target via `FRENCH_HOMOPHONES`
- `elision_variant` — normalised forms match (`je ai` / `j'ai`)
- `acoustic_miss` — nothing captured (said is empty, or accuracy = 0)
- `substitution` — everything else

**Coach word buckets** (populated at ≥ 3 attempts):
- `mastered_words` — accuracy ≥ 0.90
- `almost_there_words` — 0.70–0.89, high attempt count
- `inconsistent_words` — 0.50–0.69, high attempt count
- `tech_suspect_words` — 0.00 accuracy, high attempt count (likely mic/STT issue)
- `quick_pickup_words` — accuracy ≥ 0.85, low attempt count
- `worst_words` — all tracked words with ≥ median attempts, sorted by accuracy asc

---

## Dashboard (`static/analytics.html`)

Static file served by a one-line `FileResponse`. All data fetched after page load.

**Theme:** `<body data-theme="atelier">` — vraiKronos light/atelier design system.

**Access:** `/analytics/dashboard?key=<KEY>`. The key is read by the page from `URLSearchParams` and forwarded on every API fetch.

### Panels

| Panel | ID | Default |
|---|---|---|
| Roster | `panel-roster` | ✓ on load |
| Per-student | `panel-student-{code}` | — |
| Add Student | `panel-add-student` | — |

Student panels are cloned from `<template id="tpl-student">` at init time.

### Roster panel

**KPI row** — 4 cards with a **7 Days / 30 Days** toggle. Toggle sits directly above the card row (`kpi-section-header`).

| Card | 7d value | 30d value |
|---|---|---|
| Sessions | sum of gap-based sessions_7d; sub: avg/student | sum of sessions_30d; sub: avg/student |
| Avg Time / Session | `sum(practice_minutes_7d) / sessions_7d`; sub: total mins | — |
| Topics Studied | top 3 topics by frequency across all students (30d) | same |
| Tutor Insights | inactive students, low-score students | same with 14d inactive threshold |

**Student card grid** — `repeat(auto-fill, minmax(300px, 1fr))` responsive grid, grouped Today / Upcoming.

### Per-student panel — Tutor insights strip

The old activity KPI row (Sessions · Practice Events · Avg Score · Topics, with a
Since/30d/All window toggle) was **replaced** by an action-oriented **tutor insights**
strip at the top of the panel (`.tutor-insights`, `loadTutorInsights()`). It answers
"what do I act on this lesson?" at a glance, complementing — not duplicating — the
detailed chips in the Next Lesson tab.

- **Headline** (`.s-ti-headline`): one prioritised, explicit line derived deterministically
  from `/coach`. Priority order: check-mic (`tech_suspect_words`) → drill
  (`error_clusters.substitution`) → stuck (`trajectories.stuck_items`) → elision
  (`error_clusters.elision_variant`) → inconsistent (`inconsistent_words`) → almost-there
  (`almost_there_words`) → "no clear focus yet".
- **Count tiles** (`.ti-tiles`): To drill · Check mic · Stuck · Almost there — scannable
  counts from the same coach payload, tone-coloured (accent / error / green).

Endpoint: `/coach?access_code=` (no window toggle — coach data is lifetime). The old
`/analytics/practice` call is no longer used by the per-student panel.

### Per-student panel — 4 question-driven tabs

4 tabs framed around teacher decisions. Each tab lazy-loads its data on first open
(`data-loaded` flag); the panel loads the tutor-insights strip + Progress tab on first open
(`ensureStudentLoaded`, fired from `selectPanel`).

| Tab | Question | Content | Endpoints |
|---|---|---|---|
| **Progress** | Engaged & improving? | **Student-mirror dashboard** — the same three selectable speaking KPI cards (Performance · Precision · Words mastered) driving the level-split trend chart that the student sees on Home. Ported from `#home-view`: `buildProgKpis()` / `_progBuildChart()` (mirror of `buildHomeKpis` / `_homeBuildChart`), state scoped per panel (`panel._progKpis`). | `/analytics/progress` (access-code only — no teacher key, same as the student tool) |
| **Diagnosis** | What do they struggle with? | Word-accuracy table (accuracy bar + error-type tag + top substitution) · listen/speak mini-stats · topic coverage | `/analytics/words`, `/analytics/content` |
| **Next Lesson** ⭐ | What do I teach next? | Prioritised focus blocks: check-mic · drill words · elision · inconsistent · stuck material · almost-there | `/coach` |
| **Activity** | Drill-down / proof | Paragraph + phrase stat strips (window-sensitive, own Since/30d/All toggle) · session-history table | `/analytics/paragraph`, `/analytics/phrase`, `/analytics/sessions` |

> **Progress-tab rebuild (current).** The old single-series 8-week score-trend chart
> (`buildTrendChart` + `/analytics/trend`, recent/lifetime/delta summary, listening
> placeholder) was replaced by the student-mirror dashboard above. `buildTrendChart` and the
> per-student `/analytics/trend` wiring were removed; `/analytics/trend` (`get_score_trend`)
> remains defined but is no longer called by the dashboard.

> **Gotcha — lazily-created `.vk-kpi-card`s render invisible.** `vk-animations.js` adds
> `vk-js-animations` to `<html>`, activating `.vk-kpi-card:not(.is-visible) { opacity: 0 }`,
> and only observes cards present **at page load**. The Progress-tab cards are built lazily
> when the panel opens, so they're never observed and stay at `opacity:0` —
> visible-to-clicks, invisible-to-eyes. Fix: render them with `is-visible` already applied
> (`renderProgKpis`), the same workaround the chart card uses (`.vk-chart.is-visible`). Any
> future dynamically-created `.vk-kpi-card` hits this same trap.

### Student health dot logic

- **Grey** — no practice events yet
- **Red** — inactive > 14 days
- **Amber** — inactive 7–14 days, OR all-time avg accuracy < 45% with ≥ 10 attempts
- **Green** — none of the above

---

## Demo / Seed Data

`seed_demo_data.py` — creates 7 fake students with realistic event histories for dashboard design work. Teacher key: `teach123`.

| Student | Code | Profile |
|---|---|---|
| Marie Dupont | marie1 | Star — 14 sessions, ~80% acc, green |
| Thomas Bernard | thomas | Improving — 8 sessions, ~64% acc, green |
| Sophie Martin | sophie | Dropped off — 16d inactive, ~48% acc, red |
| Julien Roux | julien | New — 3 sessions, ~57% acc, green |
| Camille Blanc | camille | Inconsistent — 8d inactive, ~57% acc, amber |
| Antoine Petit | antoine | Plateaued — 18 sessions, ~68% acc, green |
| Léa Moreau | lea001 | Unscheduled — 9 sessions, ~64% acc, green |

---

## Dashboard Rebuild — Plan & Progress

Approach: design-first. Get each panel working with seed data before wiring real endpoints. One tab at a time.

### Completed

- **Progress tab → student-mirror dashboard + tutor-insights strip** — the Progress tab now renders the same three speaking KPI cards + level-split chart the student sees on Home (ported `buildProgKpis`/`_progBuildChart` from `index.html`, fed by `/analytics/progress`); the old activity KPI row was replaced by the `loadTutorInsights()` strip (coach-derived headline + count tiles). Removed the orphaned `buildTrendChart`. See the per-student panel sections above for detail, incl. the lazy `.vk-kpi-card` `is-visible` gotcha.
- **Session tracking** — rewrote `get_session_history()`, `get_practice_since()`, `get_roster()` to use 20-min activity-gap sessions instead of `visit_id`
- **Win-tabs position** — moved out of panel header; now sit in `kpi-section-header` directly above KPI row, matching the roster pattern
- **"Topics" KPI** — replaced "Words Tracked" with a Topics chip list that responds to the time window toggle
- **4-tab rebuild (done)** — per-student panel reorganised to Progress / Diagnosis / Next Lesson / Activity (see table above). Added 6 detail endpoints + `_window_to_since_days`/`_window_to_since_date` helpers. KPI row + all tabs wired to live data with lazy per-tab loading. Header meta-strip (last/next lesson, days since) populated from roster data. Score-trend line chart, word-accuracy table, coach focus blocks, session-history table all rendering.
- **New tracking instrumented** — `stt_confidence` on scored attempts; `text_revealed` listening-comprehension events (see Event Taxonomy). Aggregations for these are not yet built — Progress tab shows a placeholder card until data accrues.

> The phase list below predates the 4-tab rebuild and is kept for historical context. Phases 2–8 are effectively done via the rebuild; remaining genuine TODOs: Phase 1 shell cleanup (`data-mode`→`data-theme` on `<html>`, bridge-token audit, `.stats-strip` radius) and Phase 9 roster delta fields.

### Phase 1 — Shell cleanup

- [ ] Change `data-mode="light"` → `data-theme="atelier"` on `<html>` tag
- [ ] Audit inline styles using `--k-*` / `--k35-*` bridge tokens — replace with `--vk-*`
- [ ] Remove `border-radius` on `.stats-strip`
- [ ] Student panel header: populate last + next lesson dates from roster data
- [ ] Defer student panel creation — clone template on first click, not at init

### Phase 2 — Sessions tab

Wire `/analytics/sessions` (already exists). Each row: date/time · duration · attempt counts · avg score bar.

### Phase 3 — Score Trend chart

Add `GET /analytics/trend` wrapping `get_score_trend()`. 8 weeks.

> **Now (live)**: rendered as a modern single-series SVG line chart — Atelier-blue line + vertical gradient area fill, faint baseline grid, hover tooltip, left-to-right draw-in. `buildTrendChart(container, weeks)` in `static/analytics.html` renders the SVG at the container's measured pixel width (not a scaled viewBox) so dots stay circular and tooltip coords map 1:1. Null-score weeks are skipped in the line but keep their x-axis label. Replaced the original CSS bar chart (`.trend-bars`/`.tbar-*`, removed).
>
> **CSS moved → `static/vk-chart.css`** (shared). The `.atl-chart` family was extracted out of `vk-atelier-components.css` into a dedicated chart stylesheet so any surface can render it; `analytics.html` previously never linked the chart CSS (chart fell back to raw SVG) — fixed by linking `vk-chart.css`. The `.vk-line-chart`/`vk-chart` SVG styles in `vk-components.css` are unused legacy.
>
> **A redesign of this chart is in prototype** (multi-metric, multi-series, learning-not-activity). See **Progress Chart Redesign** at the end of this doc — not yet wired into the live dashboard.

### Phase 4 — Next Lesson Focus

Wire `/coach` to the Progress tab. Up to 3 priority blocks: drill words · pronunciation patterns · revisit topics.

### Phase 5 — Paragraph + Phrase tabs

Add `GET /analytics/paragraph` and `GET /analytics/phrase` with `window=since|30d|all`. Fill stats-strip + by-level table.

### Phase 6 — Words tab

Add `GET /analytics/words` wrapping `get_word_accuracy()`. Table: word · attempts · score bar · error type · substitution chips.

### Phase 7 — Content tab

Add `GET /analytics/content` combining `get_topic_coverage()` + `get_listen_speak_ratio()`.

### Phase 8 — Coaching tab

Wire `/coach` to the Coaching tab. Word buckets as collapsible sections: mastered / almost there / inconsistent / tech suspect / worst words.

### Phase 9 — Roster KPI improvements

Add `practice_minutes_30d`, `sessions_prev_7d`, `practice_minutes_prev_7d` to `get_roster()` to activate the delta trend arrows already wired in the frontend.

### Phase 10 — Lazy loading

Defer per-student panel creation to first click. Init fires one request only (`/analytics/students`).

---

## Progress Chart Redesign — prototype (NOT yet implemented)

The live Progress tab charts one thing: a single weekly **average-accuracy** line. For a
tool whose point is motivation through visible progress, that's both thin and misleading —
accuracy is one signal, it's rolled across all exercise types, and it **drops when a student
moves to harder material** even though they improved (the difficulty confound). A redesign is
being prototyped to show *learning, not activity*, for two audiences: **student** (motivation:
am I improving? what have I learned?) and **tutor** (diagnosis: what to work on).

**Status:** design-only. All of the below lives as a **mock-data prototype** in
`static/atelier-design.html` → **Data Viz** section. The live dashboard, `get_score_trend()`,
and `buildTrendChart()` are **unchanged**. Nothing here is wired to real data yet. Next step is
to rethink the Progress tab — and the analytics page as a whole — before building the backend.
Full design rationale + decisions: `~/.claude/plans/lets-plan-what-will-kind-bubble.md`.

### Metric framework (what counts as progress)

- **Accuracy** — split into **4 lines: Overall · Paragraph · Phrase · Word** (event sources:
  Paragraph = `paragraph_attempted`+`paragraph_drilled`, Phrase = `phrase_attempted`,
  Word = `word_attempted`, Overall = all). A line auto-hides when that exercise has no data.
  Paired with **CEFR level context** so a dip reads as harder material, not regression.
- **Words mastered** — a **cumulative count curve** (monotonic, always-up): distinct words now
  pronounced reliably (≥3 attempts at ≥80% hit-rate; *latched* so the student view never drops).
  The over-time complement to the lifetime `get_word_accuracy()` snapshot.
- **Pass rate** — share of attempts clearing the bar, by exercise type (secondary).
- **Activity** (attempts, active days) is treated as *engagement context*, **not** a progress
  line — it shows "you did something," not "you got better."

### Three chart variants being compared (in the prototype)

All share one renderer, `buildMultiTrendChart(container, series, opts)`, and the shared
`static/vk-chart.css` (`.atl-*`). `opts` supports `area` (gradient fill) + `areaOpacity`,
`levels` (CEFR), `weekLabels` (variable axis), and per-series `mark` (start label).
Gradient `<linearGradient>` ids are stamped with a per-render counter so multiple charts on one
page don't collide on `url(#id)`.

- **DV1 — overlaid:** all 4 type lines at once + legend. Best for cross-exercise comparison,
  busiest. CEFR level change shown as a **dashed vertical marker** on the plot (not a track).
- **DV2 — single-line focus** *(current favourite)*: one exercise line at a time via a series
  toggle; gradient area fill; cleanest read.
- **DV3 — level-split:** one exercise becomes **a separate line per CEFR level** (A2/B1/B2…),
  each spanning only its active weeks (**overlapping**, since a student works two levels at
  once), labelled at its start. Shows *within-level* progress and directly defuses the
  difficulty confound.

Controls use a **visual hierarchy**: primary **Metric** segmented control (solid-accent active)
+ secondary **Exercise** filter (smaller chips, light-tint active).

### Backend needed when this goes live (not built)

- `get_progress_trend(access_code, weeks=8)` — weekly buckets split by type (`overall`,
  `paragraph`, `phrase`, `word`) with `avg_score`, `attempts`, `pass_rate`, `level_mode`.
  Copy the week-window/bucketing from `get_score_trend()`; add a type + level dimension.
- `get_word_mastery_trend(access_code, weeks=8)` — replay `word_results` in time order →
  per-week cumulative `mastered_count` (latched), `newly_mastered`, current `struggling_count`.
- Endpoints `GET /analytics/progress` and `GET /analytics/word-mastery` (mirror `/analytics/trend`).
- Then port `buildMultiTrendChart` into `analytics.html`, retire single-series `buildTrendChart`.

### Open question (why it's paused here)

Deciding which variant(s) ship, and how the Progress tab is composed as a whole — student vs
tutor emphasis, what sits alongside the chart (trajectory strip from `get_score_trajectories()`,
words-mastered headline, etc.) — before committing to the backend shape.

---

## Student Home page (LIVE)

The live student Home (`#home-view` in `static/index.html`, `loadHomeView()`), backed by
**`get_home_data()`** via `GET /analytics/progress`. Design was prototyped in
**`static/home-design.html`** (standalone, mock data) and ported here. Plan:
`~/.claude/plans/i-feel-like-we-reactive-giraffe.md`.

**Adaptive chart axis:** the x-axis granularity scales to how long the student has been
practising, so the chart is never just 2–3 dots stretched across the full page width. The
gate is **calendar-week span** from their first active week to now (`span_weeks`):
- **span_weeks ≥ 4** → bucketed **by week**, trimmed to first in-window active week → now
  (capped at 8). A gap mid-window stays visible (a `None` point = "you paused here").
- **span_weeks < 4** → bucketed **by active day** (one point per day that has any activity,
  last 30) — denser, so a 2–3 week student shows ~8–12 dots, not 3.
- **span_weeks < 4 *and* only one active day** (a heavy first day) → falls back to **one point
  per practice session** (20-min gap) so that day still shows a curve, not a lone dot.

`axis` is `"week" | "day" | "session"`. The frontend thins x-labels when dense (keeps first +
last) and relabels the chart sub "…by day" / "…per session". Only the chart series + x-labels
switch —
scalar KPIs (value/trend/momentum/level-up) and tip signals are always computed over the
30/60-day windows. The payload carries `axis: "week" | "day" | "session"` and `labels` (the
active x-axis labels: weekly `"06/08"`, day `"6/11"`, session `"wed 2pm"`; days capped at last
30, sessions at last 10). All `levels[].points` and `words.cumulative` arrays are aligned to
`labels`. `week_labels` is kept unchanged (always the weekly labels) for back-compat. Frontend
reads `d.labels || d.week_labels`.

**Payload shape** (`get_home_data` → `{ week_labels, labels, axis, kpis, default_key, signals }`):
- `kpis.performance` / `kpis.precision`: `{ value (0–1 recent acc), trend {dir,text}|null,
  momentum (0–1, for default highlight), new_level|null, levels:[{level, points:[8 nullable]}],
  has_data }` — `levels` drives the staggered per-CEFR-level chart.
- `kpis.words`: `{ total, recent_gain, trend, momentum, cumulative:[8], has_data }`.
- `default_key`: skill with the biggest recent gain (fallback fixed order); auto-highlighted.
- `signals`: `{ new, first_level, current_level, ready_to_level_up, next_level, returning,
  last_level, gap_days, new_words, top_skill, top_trend_text, dip }` → feed `pickHomeTip()`.

**Secondary KPIs (Vocabulary + Listening) are NOT in the payload yet** — uninstrumented. The
frontend (`renderHomeSecondary`) hides the section until `get_home_data` returns a `secondary`
array. Lighting them up needs `vocab_practiced` + `comprehension_completed` events first.

### Why
Home must **show change / real progress**, not static totals — that's what motivates. The tool
is **speaking-focused** ("you're speaking French better"); accuracy is the confidence-building
evidence, but only meaningful when tied to **what each exercise tests**. Streaks were rejected
(a chore, generic). The three speaking KPIs are **primary**; Vocabulary + Listening are
**secondary** (under the chart — see below).

### The 3 KPIs (speaking only)
| KPI | Means | Source events |
|---|---|---|
| **Performance** | Speaking a whole passage, all together (sustained) | `paragraph_attempted` |
| **Precision** | Nailing one sentence — sounds & links (elision, liaison, rhythm; home of Sound Focus) | `phrase_attempted` **+** `paragraph_drilled` |
| **Words mastered** | Words you can pronounce reliably | `word_attempted` (≥3 attempts ≥80%) |

**Per-sentence drills (`paragraph_drilled`) score as Precision, not Performance** — a
single-sentence drill is sharpening, regardless of origin. NOTE: this **reclassifies
`paragraph_drilled`** from paragraph→precision vs the current `get_progress_trend()` (which
counts it as paragraph).

### Layout & interaction
- Three `.vk-kpi-card`s (fixed order Performance · Precision · Words), **change-framed** (the
  increase is the headline, the absolute number is support; `.vk-trend` badge).
- **Each card highlights to drive one chart** below it (the chart is the detail view of the
  selected KPI). On load, the **KPI with the biggest recent gain auto-highlights**; fallback to
  fixed order when there's no clear mover.
- **Chart shapes:** Performance & Precision → **staggered level-split** (one line per CEFR level,
  spanning only its active weeks, labelled at its start — leveling up doesn't read as a
  regression; maps to `get_progress_trend()`'s per-level structure). Words mastered → single
  cumulative always-up curve. Renderer = `_buildMultiTrendChart` / `.atl-chart` (`vk-chart.css`).

### Level-up badge (Performance & Precision)
`.vk-tag.is-success` "New level · B2" when a learner genuinely moves up, detected from
level-tagged attempts via the existing session grouping (`_group_events_into_sessions`, 20-min gap):
- Per session, take the **dominant level** (most attempts that session).
- **Established level** = dominant level across sessions before a higher one appears.
- **Started → badge:** higher level is dominant in **≥2 distinct sessions** (same day counts —
  morning switch + evening continue = stayed).
- **Tried → silent:** higher level dominant in only 1 session. **Reverted → silent:** dominant in
  1 session, latest session back down.
- Badge shows only while the level-up is recent (recency window), then retires.

### Secondary KPIs (under the chart)
Subordinate to the three speaking KPIs — rendered with the compact **`.vk-kpi`** tile (vs the
rich `.vk-kpi-card`), in a row beneath the chart:
- **Vocabulary** — words & expressions *practiced* (breadth/exposure, **not** mastery — learning
  unverified). Source: Flashcards / `/vocab/*`.
- **Listening comprehension** — **accuracy is the headline** (% questions correct), **time is
  context** (in the meta line). Source: `/comprehension/*`.

**Both are currently uninstrumented** (no events fired) — shown with mock data in the design.
Lighting them up needs new tracking added first (see Event Taxonomy gaps): e.g.
`vocab_practiced` (words/expressions) and `comprehension_completed` (correct/total, time), plus
timed-view duration. They are non-interactive display tiles for now (don't drive the chart).

### Smart tip (the page subtitle)
A **data-driven** one-liner that **is the header subtitle** (`#hd-sub`) — not a separate banner;
it reads as the page talking to you. Tone: **calm-encouraging**, professional, **no emoji**.
Constructive notes are included on purpose — a gentle dip message earns trust (learning isn't
linear). Rule-based `pickTip()` returns the highest-priority message that fits the signals.

**Priority-ordered library** (signals derive from the same data as the KPIs — level history,
biggest gainer, current-level accuracy, gap since last session, words mastered):

| # | Situation | Example copy |
|---|---|---|
| 0 | New learner (no data) | "Start with a few phrases, and your progress will start to take shape here." |
| 1 | Leveled up (confirmed) | "You've moved up to B2 from A2, and your accuracy is holding there. That's real progress." |
| 2 | Ready for next level (plateaued high) | "You've been consistently strong at B1 — it might be time to try B2." |
| 3 | Comeback (after a gap) | "Welcome back — you left off improving at B1. Pick up where you were." |
| 4 | A skill climbing fastest | "Your precision is climbing — up +12 pts over the last few weeks." |
| 5 | Words mastered recently | "You've locked in 19 new words this month, and counting." |
| 6 | Cross-skill nudge | "You're sharp on single sentences — try carrying that into a full paragraph." |
| 7 | Constructive dip (lowest) | "Your performance dipped a little this week — a few focused reps will bring it back. Learning isn't linear." |
| 8 | Fallback momentum | "Your precision is trending up +12 pts." |

More variants per situation were brainstormed (e.g. "Two months ago you were working at A2;
today you're speaking at B2.") — pick/rotate at build time. The mockup demos states
Climbing / Plateaued / Comeback / Quiet via a toggle.

### Wiring needed (when it goes live)
- Extend `get_progress_trend` / `get_progress_summary`: per-KPI accuracy + recent change with
  the **Precision = phrase + paragraph_drilled** reclassification; per-level series for the
  staggered charts; "biggest recent gain" pick for the default highlight (ranked relative to the
  student's own normal, recency 7d→30d→lifetime, **wins only**).
- New helper for the **level-up rule** above (session-grouped dominant level).
- Words mastered: total + recent gain (`get_word_mastery_trend` already has `mastered_30d`).
- Serve via `/analytics/progress`; port the mock into the live `#home-view` in `static/index.html`.
