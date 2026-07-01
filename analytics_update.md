# Analytics Update — Complete ✅

All items shipped. This file is retained as a reference summary.

---

## What was built

### Route + event naming
- `/shadow/*` split into `/speaking/*` (Speaking exercise) and `/shadow/*` (Shadowing exercise)
- `shadowing_time` renamed to `view_time`; now covers `speaking_phrase`, `speaking_paragraph`, and `shadow` views (fires if > 2s spent)
- `exercise_type` in `phrase_attempted`: Speaking → `"speaking"`, Shadowing → `"shadow"`

### Speaking — new tracking fields on `phrase_attempted`
- `phrase_id` — UUID per phrase, shared across all attempts on the same phrase
- `listen_count` — playback taps before submission; proxy for difficulty
- `sound_focus` — active chip at submission time (`"liaison"` / `"nasal"` / `"u_vowel"` / `null`)
- `skipped` event — fires on skip with zero attempts; avoidance signal
- `paragraph_completed` event — fires when all chunks pass

### New exercise tracking
- **Dictation** (`dictation_attempted`) — `score`, `attempt_number`, `word_results`; server recovers `level`/`topic` from `_dictation_sentences` for `/dictation/check`; `/dictation/check-inline` takes them explicitly. Frontend sends `getAnalyticsFields()` + `attempt_number` (tracked as `dictAttemptNumber` / `paraDictAttemptNumber`, reset per sentence).
- **Writing** (`writing_attempted`) — `score` (1.0 / 0.5 / 0.0) + `tip_count`; in `_SCORED_EVENTS`
- **Transformation** (`transform_attempted`) — same score/tip logic; `focus` from JS state; in `_SCORED_EVENTS`
- **Vocabulary** — `vocab_session_started` (server, on generate) · `vocab_card_quizzed` (per quiz answer) · `vocab_session_completed` (on final summary); `started` + `completed` in `_SESSION_EVENTS`
- **Comprehension** — `listen_answer_started` (server, on generate) · `comprehension_answered` (on quiz finish, `score = correct/total`); `comprehension_answered` in both `_SESSION_EVENTS` and `_SCORED_EVENTS`

### `analytics.py` — current event sets

```python
_SESSION_EVENTS = frozenset({
    'paragraph_started', 'paragraph_completed', 'chunk_listened',
    'phrase_attempted', 'paragraph_attempted', 'paragraph_drilled', 'word_attempted',
    'dictation_attempted', 'writing_attempted', 'transform_attempted',
    'vocab_session_started', 'vocab_session_completed', 'listen_answer_started',
    'comprehension_answered',
})

_SCORED_EVENTS = frozenset({
    'phrase_attempted', 'paragraph_attempted', 'paragraph_drilled', 'word_attempted',
    'dictation_attempted', 'writing_attempted', 'transform_attempted',
    'comprehension_answered',
})
```

`skipped` and `view_time` intentionally absent — passive signals only.

---

## What's still open (deferred, not forgotten)

- **Secondary KPIs on student Home** — `vocab_session_completed` and `comprehension_answered` are the source events; `get_home_data()` needs to aggregate them and return a `secondary` array for `renderHomeSecondary` to unhide the section.
