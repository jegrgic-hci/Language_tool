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
| `visit_id` | TEXT | Legacy tab ID — no longer used for session counting |

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
| `view_time` | Student exits a timed view | `view`, `duration_seconds` |
| `chunk_listened` | Paragraph chunk audio replayed | `paragraph_id`, `chunk_index`, `chunk_size` |
| `phrase_attempted` | Phrase speaking attempt submitted | `exercise_type` (`"speaking"` or `"shadow"`), `level`, `topic`, `score`, `passed`, `attempt_number`, `phrase_id`, `listen_count`, `sound_focus`, `word_results` |
| `paragraph_started` | Paragraph exercise opened | `exercise_type`, `paragraph_id`, `level`, `topic`, `sentence_count` |
| `paragraph_completed` | All chunks passed in a paragraph | `paragraph_id`, `level`, `topic`, `sentence_count` |
| `paragraph_attempted` | Paragraph chunk shadowed | `exercise_type`, `paragraph_id`, `chunk_index`, `level`, `score`, `attempt_number`, `word_results` |
| `paragraph_drilled` | Single sentence drilled | `exercise_type`, `paragraph_id`, `chunk_index`, `sentence_index`, `level`, `score`, `attempt_number`, `word_results` |
| `word_attempted` | Word drill / check | `exercise_type`, `mode`, `source`, `word`, `level`, `score`, `attempts` |
| `skipped` | Phrase skipped without an attempt | `exercise_type`, `level`, `topic`, `sound_focus`, `phrase_id` |
| `dictation_attempted` | Dictation sentence checked | `exercise_type`, `level`, `topic`, `score`, `attempt_number`, `word_results` |
| `writing_attempted` | Writing response checked | `exercise_type`, `level`, `topic`, `attempt`, `has_errors`, `score`, `tip_count` |
| `transform_attempted` | Transformation response checked | `exercise_type`, `level`, `focus`, `attempt`, `has_errors`, `score`, `tip_count` |
| `vocab_session_started` | Vocab card set generated | `exercise_type`, `level`, `subject`, `card_count` |
| `vocab_session_completed` | Vocab session finished | `exercise_type`, `level`, `subject`, `card_count`, `quiz_correct`, `quiz_total`, `quiz_score`, `is_cumulative` |
| `vocab_card_quizzed` | Single quiz card answered | `exercise_type`, `word`, `correct`, `round`, `level` |
| `listen_answer_started` | Listen & Answer passage generated | `exercise_type`, `level`, `topic`, `question_count` |
| `comprehension_answered` | Listen & Answer quiz completed | `exercise_type`, `level`, `topic`, `question_count`, `correct_count`, `score` |
| `text_revealed` | Student reveals blurred text (listening signal) | `context`, `paragraph_id`, `chunk_index`, `listens_before_reveal` |

**`word_results` format:** `[word, matched]` (legacy) or `[word, matched, said]` (current).

**`stt_confidence`** (on `phrase_attempted`, `paragraph_attempted`, `paragraph_drilled`): Web Speech API confidence, `0.0–1.0` or `null`. Chrome's `fr-FR` confidence is unreliable — treat as a coarse 3-band signal (low <0.65 · mid 0.65–0.85 · high ≥0.85). Used to disambiguate `acoustic_miss`: empty-`said` + high confidence ≈ genuine production failure; empty-`said` + low confidence ≈ STT noise.

**`phrase_id` on `phrase_attempted`:** a `crypto.randomUUID()` generated on the frontend each time a new phrase loads (`fetchNextPhrase`, `loadPhraseForDrill`). All attempts on the same phrase share the same `phrase_id`. `get_phrase_exercise_stats()` groups by `phrase_id` — one started phrase per UUID regardless of attempt count. Legacy events without `phrase_id` fall back to treating `attempt_number == 1` as a phrase start.

**`passed` on `phrase_attempted`:** stored explicitly (not derived). Current pass threshold: score ≥ 0.90.

**`text_revealed`** — the only direct listening-comprehension signal. `context`: `"phrase"` · `"paragraph"`. `listens_before_reveal` (paragraph only): trending down over weeks = ear is improving.

**`paragraph_attempted` vs `paragraph_drilled`:** `attempted` = student shadows a full chunk. `drilled` = student isolates a single struggling sentence.

**`word_attempted` modes:** `"check"` (quick timed check) · `"drill"` (10× with Mistral analysis).

**`word_attempted` sources:** `"phrase_exercise"` · `"paragraph_drill"` · `"practice_list"`.

**DB migrations run at startup** via `init_db()`: `chunk_attempted` → `paragraph_attempted`, `sentence_drilled` → `paragraph_drilled`.

**Pass thresholds:** paragraph chunk ≥ 0.70 · phrase ≥ 0.90.

---

## Session Tracking

**`visit_id` is not used for session counting.** It marked one browser tab — a student returning hours later still counted as one session.

**Current approach: activity-gap sessions.** A new session begins whenever the gap between consecutive events exceeds `SESSION_GAP_MINUTES = 20`. Computed at query time from timestamps; works retroactively.

**Events used to anchor session boundaries** (`_SESSION_EVENTS`): `paragraph_started`, `paragraph_completed`, `chunk_listened`, `phrase_attempted`, `paragraph_attempted`, `paragraph_drilled`, `word_attempted`, `dictation_attempted`, `writing_attempted`, `transform_attempted`, `vocab_session_started`, `vocab_session_completed`, `listen_answer_started`, `comprehension_answered`. `session_start` is excluded — it fires on app open with no guarantee the user did anything. `session_end` and `skipped` are excluded — passive signals only.

**A session only counts if it contains at least one scored attempt** (`_SCORED_EVENTS`: `phrase_attempted`, `paragraph_attempted`, `paragraph_drilled`, `word_attempted`, `dictation_attempted`, `writing_attempted`, `transform_attempted`, `comprehension_answered`). Groups with only passive events are dropped by `_filter_active_sessions()`. This eliminates login-and-leave noise.

Session duration = span from first to last event in the group.

---

## API Endpoints

All teacher-facing endpoints require `?key=<ANALYTICS_KEY>`. Set `ANALYTICS_KEY` in `.env` — comma-separated for multiple keys. Returns 403 if missing or invalid.

| Method | Path | Description |
|---|---|---|
| `GET` | `/analytics/dashboard?key=` | Serves `static/analytics.html` |
| `GET` | `/analytics?key=` | Aggregate stats for all students → `{ code: {...} }` |
| `GET` | `/analytics/students?key=` | Roster data for all students → `{ students: [...] }` |
| `GET` | `/analytics/sessions?key=&access_code=` | Per-session history for one student (last 20) |
| `GET` | `/analytics/word-accuracy/download?key=&access_code=` | CSV export of word accuracy |
| `POST` | `/analytics/reset?key=&access_code=` | Delete all events + coach cache for a student |
| `POST` | `/analytics/students?key=` | Create student (body: `AddStudentRequest`) |
| `PUT` | `/analytics/students/{access_code}?key=` | Update student profile fields |
| `GET` | `/coach?access_code=` | Coaching summary — returns cache if fresh, recomputes if stale |
| `POST` | `/coach/refresh?access_code=` | Force-recompute and recache coaching data |
| `GET` | `/dashboard` | Redirect → `/analytics/dashboard?key=<first key>` |

**Note:** `/coach` is not key-protected — any caller with a valid `access_code` can read it.

**Progress endpoint** (student Home + teacher Progress tab):

| Method | Path | Function |
|---|---|---|
| `GET` | `/analytics/progress?access_code=&days=7` | `get_home_data(since_days=days)` |

Access-code only — the student tool has no teacher key. Consumed by `loadHomeView()` in `static/index.html` and by `renderProgress()` in `static/analytics.html`.

`days` defaults to 30; clamped to 1–365. Controls the window for KPI values (current score avg, trend delta, words-mastered gain) — the chart always shows 8 weeks of history regardless.

**Per-student detail endpoints:**

| Method | Path | Function |
|---|---|---|
| `GET` | `/analytics/trend?key=&access_code=` | `get_score_trend()` — retained, not called by dashboard |
| `GET` | `/analytics/practice?key=&access_code=&window=since\|30d\|all` | `get_practice_since()` — retained, not called by dashboard |
| `GET` | `/analytics/paragraph?key=&access_code=&window=since\|30d\|all` | `get_paragraph_exercise_stats()` |
| `GET` | `/analytics/phrase?key=&access_code=&window=since\|30d\|all` | `get_phrase_exercise_stats()` |
| `GET` | `/analytics/words?key=&access_code=` | `get_word_accuracy()` |
| `GET` | `/analytics/recent-struggles?key=&access_code=&sessions=3` | `get_recent_struggles()` — words <50% in last N sessions |
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
| `get_roster()` | All students with roster card stats; sorted by `days_until_next` asc, no-schedule students last. Returns per-student: `sessions_7d/30d`, `sessions_prev_7d/30d`, `practice_minutes_7d/30d`, `practice_minutes_prev_7d/30d`, `avg_phrase_score_7d/30d`, `avg_phrase_prev_7d/30d`, `avg_para_score_7d/30d`, `avg_para_prev_7d/30d`. Prior windows: 7d prev = days 8–14, 30d prev = days 31–60. |

### Aggregate queries

| Function | Returns |
|---|---|
| `get_analytics()` | `{code: _aggregate(rows)}` for every code with events |
| `get_session_history(access_code, limit=20)` | Per-session summaries via gap analysis — attempts, scores, new/revisited words, duration |
| `get_word_accuracy(access_code, min_attempts=3)` | Per-word accuracy + top substitutions + error type classification |
| `get_paragraph_exercise_stats(access_code, since_days=None)` | Started / completed / phrases drilled / words practiced — overall + by level |
| `get_phrase_exercise_stats(access_code, since_days=None)` | Started / completed / stuck / avg attempts to complete — overall + by level. Groups by `phrase_id`; one started phrase per UUID regardless of attempt count. |
| `get_topic_coverage(access_code)` | Attempts + avg score per topic, sorted by attempts desc |
| `get_listen_speak_ratio(access_code)` | Listens vs speak attempts + ratio + avg replays per chunk |
| `get_score_trend(access_code, weeks=8)` | Weekly score buckets (8 weeks, incl. empty) + recent/lifetime avgs + delta. Not currently wired to dashboard. |
| `get_progress_trend(access_code, weeks=8)` | Weekly score+pass series split by exercise type and CEFR level, null-padded per week. Powers the student Home and teacher Progress tab charts. Pass thresholds: paragraph 0.70, phrase/word 0.90. |
| `get_word_mastery_trend(access_code, weeks=8)` | Cumulative words-mastered curve, latched (never decreases). A word masters at ≥3 attempts & ≥80% hit-rate. Also returns `total_mastered`, `newly_mastered` (last week), `mastered_30d`. |
| `get_progress_summary(access_code)` | Three Home-landing KPI cards: words mastered, within-level accuracy trend, avg session length. |
| `get_home_data(access_code, weeks=8, since_days=30)` | Full payload for `/analytics/progress` — KPIs, chart series, signals, adaptive axis. `since_days` sets the recency window for KPI values and trend deltas (cutoff_recent = today−days, cutoff_prior = today−days×2). Response includes `period_days`. |
| `get_score_trajectories(access_code)` | Per-item mastered / improving / plateaued / stuck counts. Each `stuck_item` includes `text` (sentence reconstructed from `word_results`) alongside `key`, `level`, `best`, `last`, `attempts`. |
| `get_sentence_drill_breakdown(access_code)` | Drill attempts + scores by difficulty level |

### Session helpers

| Function | Description |
|---|---|
| `_split_session_groups(ts_list)` | Splits sorted timestamp list into `(start_ts, end_ts)` pairs using `SESSION_GAP_MINUTES` |
| `_group_events_into_sessions(rows)` | Groups `(ts, event_type, payload_dict)` rows into session buckets by gap |
| `_filter_active_sessions(groups)` | Drops session groups with no `_SCORED_EVENTS` — removes login-and-leave noise |

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

**Phonetic categories** (`phonetic_lookup.py` + `data/Lexique383.tsv`):
Each word in `get_word_accuracy()` carries a `phonetic_categories` list derived from Lexique383. Populated at import time — one load of the 25 MB TSV, then a dict lookup per word. Categories:
- `nasal` — contains [ɑ̃], [ɔ̃], [ɛ̃], or [œ̃] (Lexique chars `@`, `§`, `5`, `1`)
- `u_sound` — contains [y] (char `y`; distinct from [j] char `j`)
- `eu_sound` — contains [ø] or [œ] (chars `2`, `9`)

A word can belong to multiple categories. Words not in Lexique return `[]`.

**`phonetic_struggles`** (in `get_coach_data`): per-category stats for words with accuracy < 0.65 and ≥ 3 attempts; only included when ≥ 2 qualifying words exist. Shape: `{ "nasal": { words: [...], avg_accuracy: 0.25, count: 3 }, ... }`. Powers both the Diagnosis tab grouping and the Insights phonetic recommendations.

**Coach cache invalidation**: `get_cached_coach` returns `None` (forcing recompute) if the cached payload predates the `phonetic_struggles` field — one-time migration on first load per student.

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

**Access:** `/analytics/dashboard?key=<KEY>`. The key is read from `URLSearchParams` and forwarded on every API fetch.

### Panels

| Panel | ID | Default |
|---|---|---|
| Roster | `panel-roster` | ✓ on load |
| Per-student | `panel-student-{code}` | — |
| Manage Students | `panel-manage-students` | — |

Student panels are cloned from `<template id="tpl-student">` at init time. `static/students.html` has been removed — all student management now lives inside `panel-manage-students`.

### Roster panel

**KPI row** — 4 cards with a **7 Days / 30 Days** toggle. All 4 cards show a `↑/↓ ±X% vs prev period` delta — 7d compares to the prior 7d (days 8–14), 30d compares to the prior 30d (days 31–60).

| Card | Value | Delta |
|---|---|---|
| Sessions | Total gap-based session count across cohort; sub: avg/student | vs prior same-length window |
| Avg Time / Session | `sum(practice_minutes) / sessions`; sub: total mins | vs prior same-length window |
| Performance | Cohort avg paragraph accuracy (`avg_para_score_7d` or `avg_para_score_30d`) | vs prior same-length window |
| Precision | Cohort avg phrase accuracy (`avg_phrase_score_7d` or `avg_phrase_score_30d`) | vs prior same-length window |

Performance = `paragraph_attempted` scores. Precision = `phrase_attempted` scores. Aligns with the same definitions used in the student Home and teacher Progress tab.

**Student card grid** — `repeat(auto-fill, minmax(300px, 1fr))`, grouped Today / Upcoming.

Each roster card shows: health dot + name + next lesson, an optional "No practice since last lesson" warning, sessions/pratique/précision stats for the window period, topic tags, and a footer insight.

**Student health dot:**
- Grey — no practice events yet
- Red — inactive > 14 days
- Amber — inactive 7–14 days, OR all-time avg accuracy < 45% with ≥ 10 attempts
- Green — none of the above

### Per-student panel — secondary navigation

The tab bar lives inside `.panel-header` and stays sticky as the user scrolls. **Tab order: Insights | Diagnosis | Progress | Activity.** Insights is the default on panel open (`ensureStudentLoaded` → `loadTab(panel, 'insights')`).

### Per-student panel — 4 tabs

| Tab | Question | Content | Endpoints |
|---|---|---|---|
| **Insights** | What do I do this lesson? | 5 clickable indicator cards (each expands an inline detail panel) + Lesson focus + Recommendations + Recent struggles | `/coach`, `/analytics/progress`, `/analytics/paragraph`, `/analytics/phrase`, `/analytics/content` |
| **Diagnosis** | What do they struggle with? | Filter chips + word accuracy table · listen/speak ratio · topic coverage | `/analytics/words`, `/analytics/content` |
| **Progress** | Engaged & improving? | Student-mirror dashboard — 3 speaking KPI cards + level-split trend chart | `/analytics/progress` |
| **Activity** | Drill-down / proof | Paragraph + phrase stat strips (Since/30d/All toggle) · session-history table | `/analytics/paragraph`, `/analytics/phrase`, `/analytics/sessions` |

**Insights tab** (`renderInsights`, parallel fetch of all 5 endpoints):

All endpoints are fetched in parallel via `Promise.allSettled`. Each indicator renders independently; a failed fetch shows grey/no-data rather than breaking the tab.

#### Indicator strip (`.s-indicators .ind-strip`)

5 always-present cards, each with a status dot (green / amber / red / grey) and a one-line reading. Every card is **clickable** — clicking expands an inline detail panel (`.s-ind-detail`) below the strip. Clicking the active card again collapses it (toggle). The active card receives `.is-active` styling (accent border + tint).

| Indicator | Builder | Data source | Green | Amber | Red | Grey |
|---|---|---|---|---|---|---|
| **French sounds** | `_indSounds` | `/coach` | No pattern detected | Elision errors ≥2, or phonetic avg 40–70% | Phonetic avg <40%, or mic issues ≥2 | No data | Shows all `phonetic_struggles` categories below 50% (e.g. "Nasals 35% · [u] 48% — multiple patterns"); falls back to worst single category if none are below 50% |
| **Level progress** | `_indLevel` | `/analytics/progress` | Improving / new level / ready to advance | Dipped recently | Declining | Steady or no data |
| **Exercise mix** | `_indMix` | `/analytics/paragraph` + `/analytics/phrase` (30d) | Both paragraph + phrase practiced | One type >90% of activity | — | No data |
| **Listen / speak** | `_indBalance` | `/analytics/content` | Ratio 0.5–5× (with ≥5 speaking attempts) | Listening-heavy (>5×) or speaking without listening (<0.5×) | — | <5 attempts |
| **Consistency** | `_indConsistency` | Roster data (`window._rosterStudents`) | 2+ sessions this week | 1 session this week, or last practiced 4–7 days ago | No practice in >14 days | No practice recorded |

#### Indicator detail expansion (`.s-ind-detail`, `_buildIndDetail()`)

Appears between the indicator strip and the recommendations when a card is clicked. Built from `panel._insightsData` (the parallel-fetched data stored during `renderInsights`). Each key shows a brief data summary and a "Go to [Tab] →" navigation button.

| Key | Detail content | Navigation |
|---|---|---|
| `sounds` | Phonetic category breakdown table (category · word count · avg accuracy bar), or elision/mic error count if no phonetic data | "See the words in Diagnosis →" — carries a `diagFilter` to pre-select the matching chip |
| `level` | Current level + Performance % + Precision % (with trend text) | "See full trend in Progress →" |
| `mix` | Paragraphs started · Phrases started · Para/phrase % split | "See activity breakdown →" in Activity |
| `balance` | Listens · Speak attempts · Listen/speak ratio | "See full breakdown in Diagnosis →" |
| `consistency` | Sessions this week · Days since practice · Practice minutes this week | "See session history →" in Activity |

#### Lesson focus (`.s-ti-recs`, `_buildLessonSummary()`)

A single sentence in a tinted accent block at the top of `.s-ti-recs`. Synthesises coach + progress signals into one verb-first directive for today's session. Priority: mic issues → multiple sounds <50% → single sound struggling → ready to level up → elision errors → stuck items → strong momentum → no clear issue. When stuck items fire, up to 2 sentence texts are quoted inline.

#### Recent struggles (`_buildRecentStruggles()`)

Word chips rendered below the recommendations. Fetched from `GET /analytics/recent-struggles?access_code=` (`get_recent_struggles(access_code, sessions=3, threshold=0.50)`). Each chip shows the word, its accuracy percentage in red, and the top substitutions said instead (e.g. "→ su, sous"). Only words with ≥2 attempts in the last 3 sessions are included. Shows a no-data message if nothing qualifies.

#### Recommendations (`.s-ti-recs`, `_buildRecs()`)

2–3 stacked `.focus-block` elements below the indicator strip, each a verb-first directive + teaching rationale. A **"See the words →"** button on relevant recs navigates to the Diagnosis tab and pre-selects the matching filter chip.

| Priority | Rec | Filter carried |
|---|---|---|
| 1 | `tone-warn` — **Verify mic setup** (`tech_suspect_words >= 2`) | `acoustic_miss` |
| 2 | `tone-plain` — **Step back a level** (`stuck_items >= 2`) — lists up to 3 stuck sentences verbatim with best score and attempt count | — (no link) |
| 3 | `tone-accent` — **Address elision as a structural rule** (`elision_variant >= 2`) | `elision_variant` |
| 4 | `tone-accent` — **Dedicate lesson time to [named sound]** / **Multiple sound patterns need work** | `phonetic:{worst-cat}` |
| 5 | `tone-plain` — **Stay at current level, build volume** (`inconsistent_words >= 3`) | `inconsistent` |
| 6 | `tone-plain` — **Explore the underlying sound pattern** (`substitution >= 2`, no phonetic cat) | `substitution` |
| 7 | `tone-green` — **Increase difficulty** (`almost_there >= 3`, `stuck < 2`) | — (no link) |

Falls back to empty if no triggers fire. Max 3 recs shown.

#### Diagnosis tab (`renderDiagnosis`)

**Word accuracy filter chips** (`.diag-chips`, `_renderDiagWords(panel)`) — a row of chips above the word table. Only chips with at least one matching word are rendered; "All" is always present.

| Chip | Filter key | Matches |
|---|---|---|
| All | `null` | Clears filter — shows all words in default order |
| Elision | `elision_variant` | `error_type === 'elision_variant'` |
| Mic issues | `acoustic_miss` | `error_type === 'acoustic_miss'` |
| Substitution | `substitution` | `error_type === 'substitution'` |
| Inconsistent | `inconsistent` | `accuracy` 50–70% |
| Nasals | `phonetic:nasal` | `phonetic_categories` includes `nasal` |
| [u] sound | `phonetic:u_sound` | `phonetic_categories` includes `u_sound` |
| [eu] sound | `phonetic:eu_sound` | `phonetic_categories` includes `eu_sound` |

When a filter is active, matching words sort to the top (tinted rows), followed by an "Other words" separator and the remaining words. The active chip state persists on `panel._diagnosisFilter` — cleared when "All" is clicked.

**Cross-tab navigation:** navigating from Insights (indicator detail or "See the words →" rec link) sets `panel._diagnosisFilter` before switching tabs. If Diagnosis is already loaded (`panel._diagnosisWords` is set), `_renderDiagWords` is called immediately to re-sort; otherwise the filter is applied when the tab loads for the first time. The word data itself is never re-fetched — it is stored on the panel as `panel._diagnosisWords` on first load.

**Progress tab** (`renderProgress`, `/analytics/progress`): mirrors the student Home exactly — same 3 KPI cards (Performance · Precision · Words mastered), same level-split chart, same `buildProgKpis` / `_progBuildChart` logic scoped per panel. Has a **Last 7 Days / Last 30 Days** toggle (7 days first, default) that re-fetches with `?days=7` or `?days=30`. Selected window stored on `panel._progDays`; toggle state (`data-progwin` buttons) wired in `initWinTabs`.

> **Gotcha — lazily-created `.vk-kpi-card`s render invisible.** `vk-animations.js` activates `.vk-kpi-card:not(.is-visible) { opacity: 0 }` and only observes cards present at page load. Cards built lazily when a panel opens are never observed and stay invisible. Fix: render them with `is-visible` already applied (`renderProgKpis`). Any future dynamically-created `.vk-kpi-card` hits this same trap.

### Manage Students panel (`panel-manage-students`)

Opened via the **Admin → Manage Students** sidebar item or the "+ Add Student" button in the roster panel header. Replaces the deleted `static/students.html`.

**Sidebar footer** — a **"Go to Tool ↗"** link (`href="/"`, `target="_blank"`) lets the teacher open the student-facing tool in a new tab without leaving the dashboard.

**Auth:** uses `_mgmtFetch()` — a JWT-aware wrapper that reads `ft_jwt` from `localStorage`, attempts a token refresh on 401 via `/auth/refresh`, and shows a login-link message if unauthenticated. Teacher endpoints require JWT; the rest of the dashboard only needs the API key.

**Add Student form** — email (required) + name. POSTs to `POST /teacher/students`. On success, opens the credential modal showing the generated email, username, and temp password. The student is prompted to change the password on first login.

**Roster table** columns: Student (name + email), Access Code, Status (Active / Paused badge), Last Active, Events, Actions.

**Actions per row:**

| Button | Endpoint | Behaviour |
|---|---|---|
| Analytics | — | `selectPanel('student-{code}')` — jumps to that student's analytics panel |
| Reset Password | `POST /teacher/students/{id}/reset-password` | Confirm → shows credential modal with new temp password |
| Pause | `PATCH /teacher/students/{id}/status` `{is_active:0}` | Confirm → disables login |
| Reactivate | `PATCH /teacher/students/{id}/status` `{is_active:1}` | Confirm → re-enables login |
| Remove | `DELETE /teacher/students/{id}` | Confirm → deactivates account |

**Credential modal** (`mgmt-cred-modal`): shows email + username + temp password, each with an individual Copy button and a "Copy All" button. Not shown again after dismiss — the teacher must copy before closing.

**Confirm modal** (`mgmt-confirm-modal`): single-action destructive confirm used for Reset Password, Pause/Reactivate, and Remove.

**Toast** (`mgmt-toast`): 3-second ephemeral feedback for action success/failure. Error state shown in `--vk-error` colour.

---

## Demo / Seed Data

`seed_demo_data.py` — creates 7 fake students with realistic event histories. Teacher key: `teach123`.

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

## Student Home page

The live student Home (`#home-view` in `static/index.html`, `loadHomeView()`), backed by `get_home_data()` via `GET /analytics/progress`.

**Adaptive chart axis:** x-axis granularity scales to how long the student has been practising:
- `span_weeks ≥ 4` → bucketed **by week**, capped at 8
- `span_weeks < 4` → bucketed **by active day** (last 30)
- `span_weeks < 4` and only one active day → **by session** (20-min gap, last 10)

`axis` is `"week" | "day" | "session"`. Frontend thins x-labels when dense. `week_labels` kept for back-compat; frontend reads `d.labels || d.week_labels`.

**Payload shape** (`get_home_data` → `{ week_labels, labels, axis, kpis, default_key, signals, period_days }`):
- `kpis.performance` / `kpis.precision`: `{ value, trend, momentum, new_level|null, levels:[{level, points:[]}], has_data }`
- `kpis.words`: `{ total, recent_gain, trend, momentum, cumulative:[], has_data }`
- `default_key`: skill with biggest recent gain; auto-highlighted on load
- `signals`: `{ new, first_level, current_level, ready_to_level_up, next_level, returning, last_level, gap_days, new_words, top_skill, top_trend_text, dip }` → feeds `pickHomeTip()`

**Secondary KPIs (Vocabulary + Listening) are not yet wired to the Home chart.** `renderHomeSecondary` hides the section until `get_home_data` returns a `secondary` array. The events that will power these cards already fire: `vocab_session_completed` (Vocabulary KPI) and `comprehension_answered` (Listening KPI). Wiring `get_home_data` to aggregate them is the remaining step.

### The 3 KPIs (speaking only)

| KPI | Means | Source events |
|---|---|---|
| **Performance** | Speaking a whole passage together (sustained) | `paragraph_attempted` |
| **Precision** | Nailing one sentence — sounds, elision, liaison | `phrase_attempted` + `paragraph_drilled` |
| **Words mastered** | Words the student can pronounce reliably | `word_attempted` (≥3 attempts, ≥80% hit-rate) |

`paragraph_drilled` scores as Precision, not Performance — a sentence drill is sharpening regardless of origin.

### Chart

Performance & Precision → **level-split chart** (one line per CEFR level, spanning only its active weeks, labelled at start). Words mastered → single cumulative always-up curve. Renderer: `_buildMultiTrendChart` / `.atl-chart` (`vk-chart.css`).

### Level-up badge

`.vk-tag.is-success` "New level · B2" fires when a higher level is dominant in ≥2 distinct sessions. One session at a higher level → silent. Reverted in latest session → silent.

### Smart tip

Rule-based `pickHomeTip()` returns a data-driven one-liner as the page subtitle (`#hd-sub`). Priority: new learner → leveled up → ready to level → comeback → climbing skill → new words → cross-skill nudge → constructive dip → fallback momentum.
