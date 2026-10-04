# Vocabulary Feature

## Design philosophy
Reading definitions is passive and doesn't train the ear. This feature is built around **oral comprehension** — the stimulus and feedback are primarily auditory, not textual. English translation is always available but hidden behind a button tap.

## Session structure

### Standard mode (5 / 10 / 15 / 20 words) — 2 rounds

| Round | Name | Format |
|---|---|---|
| 1 | **Exposure** | All cards shown as a scrollable list. For each: play the word audio, play the French definition audio, then Reveal shows the definition plus an "In a sentence" example (own play button) showing how the word is used — idioms in a realistic situation, verbs with their usual construction, adjectives with a typical noun. One Translate button shows English for both. No testing — pure intake. |
| 2 | **Recall** | One card at a time. Alternates two quiz types, interleaved and shuffled: **Listen & identify** — definition plays automatically, word is hidden, pick the matching word from 6 options. **Read & find** — word is shown, 6 definition rows each play a different definition audio, select the correct one. (The hub's How to answer choice can narrow this to one type.) |

After Recall: session ends, returns to hub.

### Cumulative mode (20 words, 4 batches of 5) — 3 rounds per batch

| Round | Name | Format |
|---|---|---|
| 1 | **Exposure** | 5 new words, listed |
| 2 | **Recall** | Quiz on those 5 words (alternating listen-identify / read-find) |
| 3 | **Review** | Quiz across the growing pool of all words seen so far |

Batch 1 skips the standalone Review (its Recall already covers all cards). Batches 2–4 trigger Review before the next batch starts. After the 4th batch Review: session ends, returns to hub.

## Spaced repetition (Review your words)
A slim banner at the top of the hub content, above the setup rows (an alternate path, not a setting), titled "Spaced repetition", in two states:
- **No words yet:** "Practice words in Flashcards to build your review deck" + info icon (expands the explainer). No button.
- **Words saved:** "Practice words you've already learned to reinforce them" + info icon, then on the right the status ("12 due today" / "Next review tomorrow · 5 words") and Start review (disabled when nothing is due).
Account-tied, one deck per study language, hidden in teach mode and when signed out.
- **Joining:** after every regular Recall (standard session or a cumulative batch), all its words are added via `POST /vocab/review/add` — new words due tomorrow. A word already in the deck keeps its schedule unless it was missed again (back to the start). The set's decoys are saved with each word.
- **Schedule:** `VOCAB_REVIEW_INTERVALS = [1, 3, 7, 14, 30]` days (`analytics.py`). Right in a review → next interval (the last repeats); missed → back to the start, due tomorrow. Dates are UTC days.
- **Review session:** `GET /vocab/review/due` → up to 20 due words (most overdue first) + up to 30 other saved words and the saved decoys for wrong options. Recall only (no Exposure, stepper hidden), using the How to answer setting. A word counts as remembered only if every question on it was right first time; second tries don't count. Results go to `POST /vocab/review/results`; summary "X of Y remembered". Event `vocab_review_completed` (`words`, `remembered`).
- Storage: `vocab_review` table (`user_id, lang, word_key` key; `card`, `decoys` JSON; `stage`, `due_at`, `right_n`, `missed_n`).

## End screen (session + review results)
`_renderVocabResults()` into `#vocab-results` (the old `.vocab-summary-card` now only serves cumulative "Block complete" transitions). Hero: the brand mark as a celebration (`_vrAtomSvg()`, inline brand-mark.svg geometry) tinted by medal tier — gold ≥90%, silver 70–89%, bronze 50–69% (`--pa-medal-*`), below 50% `--pa-fail` — springs in; for a medal its electrons spin round the orbit and sparks burst out (12 / 8 / 6); below 50% it settles in quietly with no celebration. Beside it: label, the percentage as a display numeral, sentence headline, remembered / to-review counts, actions; then **To review** and **Remembered** word lists (play button, word, definition, due chip), rows fading in one after another. All motion off under reduced motion.
- Session: word-level — a word is "to review" if any question on it was missed; "X of N words right first time"; when signed in, "All N words join your review deck" and missed words carry "Review tomorrow".
- Review: "X of N remembered"; each word's chip is its new due date from `/vocab/review/results` (`due` map) — Tomorrow / In 3 days / In 1 week / In 2 weeks / In a month.

## How to answer (hub choice)
The hub's **How to answer** row picks the Recall/Review question type: **Hear the definition, pick the word** (`listen-identify`), **See the word, hear the definitions** (`read-find`), **Hear the definition, write the word** (`write`, English only, chip hidden in French) or **Mixed** (default). Remembered per device in localStorage `ft_vocab_answer_mode` (`_vocabAnswerMode`); a saved `write` runs as Mixed in French (`_vocabEffectiveAnswerMode()`).
- One chosen way: each word is asked once (10 words = 10 questions). A miss in Recall comes back once at the end of the round, labelled "Second try"; that retry doesn't count toward the score.
- Mixed: each word asked two ways as before (French: listen-identify + read-find; English: write + read-find).
- Review (cumulative) uses the same type(s) and still repeats a missed word until it's right.
- All three types score through `_vocabRecordAnswer()`; `vocab_card_quizzed` carries `quiz_type` (`word` / `definition` / `write`) and `retry`.

## Recall card types

**Listen & identify** (`listen-identify`): Word row is hidden; the learner taps play to hear the definition (no auto-play), then the 6 word options appear. Selects the correct word. On answer: banner (correct/wrong) + definition text + example + optional English.

**Read & find** (`read-find`): Word and part of speech shown. 6 definition rows, each an M3 list item: leading radio | divider | tonal play button (plays that definition, never selects), "Definition N" + a status line (Tap play to listen / Playing… / Heard). Tapping the row selects it; Confirm commits. On answer: banner + revealed definition + example + optional English.

**Write** (`write`, English only): replaces Listen & identify in English decks (same prompt; seeing the word among choices first would give away its spelling). Learner taps play to hear the definition, types the word, Check. Graded by `POST /vocab/check-typed` → `exact` / `close` / `wrong`: case, punctuation, hyphens, accents and US/UK spelling ignored (shared English normalizer), a leading the/a/an/to optional, one-letter typo or swapped pair (`len ≥ 4`) is `close` — counted right, banner shows the spelling. Known gap: a different real word one letter away (deceive/receive) also counts as `close`. Tracked as `vocab_card_quizzed` with `quiz_type: 'write'`.

Both choice quiz types build their rows with `_vcRowHtml()` (Listen & identify rows show the word, no status line); the row is `role="radio"` in a `radiogroup`, Enter/Space selects.

**Options (both choice types): 6** — the answer, 4 other words from the set, and 1 **decoy** from outside it (`_buildChoices()`). `/vocab/generate` returns `decoys` alongside `cards` (`max(3, min(6, count // 3))` of them: same level and subject, not a card or a near-synonym; `{word, french_definition}`, the definition in the language being learned). One decoy is picked at random per question; decoys are saved with a cumulative set so a resumed set keeps them. Without decoys (older saved sets, or the model returned none) the set fills all 5 wrong options.

Set distractors are drawn from the current batch (`_vocabCards`) during standard Recall, and from the full cumulative pool (`_vocabCumulativePool`) during Review.

## Backend
- **Endpoint**: `POST /vocab/generate`
- **Model**: `mistral-large-latest`
- **Request**: `{ level, subject, count }` — count is 5/10/15/20 for standard mode, 20 for cumulative
- **Response**: `{ cards: VocabCard[], decoys: VocabDecoy[] }` (decoy = `{ word, french_definition }`)
- **VocabCard fields**: `word`, `part_of_speech`, `usage` (courant/familier/soutenu), `french_definition`, `english_definition`, `example_sentence`, `english_translation`

## Frontend
- Nav label: **Vocabulary → Flashcards**
- **Hub** (`#vocab-hub`): CEFR level chips, subject chips, count chips (5/10/15/20/Cumulative), custom subject input, Generate button
- **Card view** (`#vocab-view`): 2-step (or 3-step cumulative) round stepper, card area
- TTS: word and definition audio via `/tts` → `edge-tts` (`fr-FR-DeniseNeural`)
  - `_vocabTTSPromise()` — chainable (returns Promise resolving on audio end), used in Exposure for sequential word → definition playback
  - `_vocabTTS()` — fire-and-forget, used in Recall auto-play on card load

## Key JS state

| Variable | Meaning |
|---|---|
| `_vocabRound` | 0 = Exposure, 1 = Recall, 2 = Review (cumulative only) |
| `_vocabCards` | Current batch (5 cards in cumulative, full set in standard) |
| `_vocabRoundCards` | `{card, type}` deck for Recall/Review; plain card array for Exposure |
| `_vocabIsCumulative` | True when cumulative mode selected |
| `_vocabCumulativePool` | Growing slice of all 20 generated cards, expanded after each batch |
| `_vocabBatchIdx` | Current batch index (0–3) |
| `_vocabAllGenerated` | All 20 cards fetched upfront in cumulative mode |

## Subjects available (hub chips)
Daily life · Idioms · Emotions · Food · Travel · Work · Health · Culture · Marseille · Slang · custom input

## Analytics
`vocab_session_started` fires on `/vocab/generate` with `level`, `subject`, `card_count`. In `_SESSION_EVENTS` only (not scored). Frontend wiring pending — needs `...getAnalyticsFields()` added to the generate fetch body.

## Pending / ideas
- `vocab_session_completed` event when Recall finishes (unlocks secondary KPI on student Home)
- Per-card quiz accuracy (`vocab_card_quizzed` event) for spaced-repetition surfacing
- Pronunciation round: after Recall, shadow each word (speaking mode for vocab)
