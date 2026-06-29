# Vocabulary Feature

## Design philosophy
Reading definitions is passive and doesn't train the ear. This feature is built around **oral comprehension** — the stimulus and feedback are primarily auditory, not textual. English translation is always available but hidden behind a button tap.

## Session structure

### Standard mode (5 / 10 / 15 / 20 words) — 2 rounds

| Round | Name | Format |
|---|---|---|
| 1 | **Exposure** | All cards shown as a scrollable list. For each: play the word audio, play the French definition audio, read the definition and example sentence. No testing — pure intake. |
| 2 | **Recall** | One card at a time. Alternates two quiz types, interleaved and shuffled: **Listen & identify** — definition plays automatically, word is hidden, pick the matching word from 4 text chips. **Read & find** — word is shown, 4 numbered buttons each play a different definition audio, select the correct one. |

After Recall: session ends, returns to hub.

### Cumulative mode (20 words, 4 batches of 5) — 3 rounds per batch

| Round | Name | Format |
|---|---|---|
| 1 | **Exposure** | 5 new words, listed |
| 2 | **Recall** | Quiz on those 5 words (alternating listen-identify / read-find) |
| 3 | **Review** | Quiz across the growing pool of all words seen so far |

Batch 1 skips the standalone Review (its Recall already covers all cards). Batches 2–4 trigger Review before the next batch starts. After the 4th batch Review: session ends, returns to hub.

## Recall card types

**Listen & identify** (`listen-identify`): Definition audio auto-plays. Word row is hidden. Student selects the correct word from 4 text chips (target + 3 distractors from the current card pool). On answer: banner (correct/wrong) + definition text + example + optional English.

**Read & find** (`read-find`): Word and part of speech shown. 4 numbered buttons each play a different definition audio (target + 3 distractors). Student must hear at least one before the Confirm button activates. On answer: banner + revealed definition + example + optional English.

Distractors are drawn from the current batch (`_vocabCards`) during standard Recall, and from the full cumulative pool (`_vocabCumulativePool`) during Review.

## Backend
- **Endpoint**: `POST /vocab/generate`
- **Model**: `mistral-large-latest`
- **Request**: `{ level, subject, count }` — count is 5/10/15/20 for standard mode, 20 for cumulative
- **Response**: `{ cards: VocabCard[] }`
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
- Spaced repetition: persist missed cards to SQLite and resurface in future sessions
- Pronunciation round: after Recall, shadow each word (speaking mode for vocab)
