# Vocabulary Feature

## Design philosophy
Reading definitions is passive and doesn't train the ear. This feature is built around **oral comprehension** — the stimulus and the feedback are primarily auditory, not textual. English translation is always available but hidden behind a button tap.

## Session structure (4 rounds)

| Round | Name | Format |
|---|---|---|
| 1 | Exposure | Hear the word spoken, then hear its French definition. Text revealed only after audio plays. No testing. |
| 2 | Path A | Definition plays automatically. Word is hidden. Pick the matching word from 4 text chips. |
| 3 | Path B | Word shown. 4 numbered play buttons — each plays a different definition audio. Pick by number. Answer row only appears after at least one definition is heard. |
| 4 | Quiz | Alternating Path A / Path B, cards shuffled. Scored. |

After the quiz: correct / missed counts shown. "Study missed" restarts the full 4-round cycle with only the missed cards.

## Backend
- **Endpoint**: `POST /vocab/generate`
- **Model**: `mistral-large-latest`
- **Request**: `{ level, subject, count }` — level is CEFR (A1–C2), count capped at 4–12
- **Response**: array of `VocabCard` objects
- **VocabCard fields**: `word`, `part_of_speech`, `usage` (courant/familier/soutenu), `french_definition`, `example_sentence`, `english_translation`
- Definition quality rules in `_VOCAB_SYSTEM` prompt: A1/A2 get simpler French definitions, C1/C2 get register variation and nuanced expressions

## Frontend
- Nav section: **Vocabulary → Flashcards** button in sidebar
- **Hub** (`#vocab-hub`): CEFR level chips, subject chips, custom subject input, Generate button
- **Card view** (`#vocab-view`): 4-dot round indicator bar, card with per-round sections
- TTS: word and definition audio via existing `/tts` + `edge-tts` (`fr-FR-DeniseNeural`)
- `_vocabTTSPromise()` — chainable TTS (returns Promise resolving on audio end), used in Exposure for sequential word → definition playback
- `_vocabTTS()` — fire-and-forget TTS, used in Path A auto-play on card load

## Subjects available (hub chips)
Daily life · Idioms · Emotions · Food · Travel · Work · Health · Culture · Marseille · Slang · custom input

## Pending / ideas
- Spaced repetition: persist missed cards to SQLite and resurface them in future sessions
- Pronunciation round: after quiz, hear each word and repeat it (shadowing mode for vocab)
- Import from chat: words clicked in conversation added to a personal vocab list
- Progress tracking per subject/level over time (analytics hook already in place)
