# Future Updates

Remaining pronunciation improvements from code review (IPA tips and phoneme confusion map already implemented).

---

## High Priority

### Liaison tracking
Detect liaison opportunities in target phrases (consonant-final word + vowel-initial next word) and check whether the transcription reflects the liaison sound. Currently missing liaisons score identically to mispronunciations, but they're a distinct error type. Return a specific liaison tip rather than a generic mismatch card.

Especially relevant for Marseille French where liaison patterns differ from standard Parisian.

### Spaced repetition for problem words
The analytics table already tracks per-word accuracy. Add a query that identifies the 2–3 lowest-accuracy words from recent sessions and surface them as a warm-up drill at session start. Removes the need for manual practice list curation and creates a natural feedback loop.

### Confidence threshold refinement (pending feature from CLAUDE.md)
Replace the binary 0.65 gate with a two-tier system:
- **0.65–0.80**: amber indicator, let user choose to proceed or re-record
- **<0.50**: auto-reject before sending, prompt retry

Pass `confidence: float` in the chat request body and use a more targeted coherence prompt at low confidence to identify the specific likely-mispronounced word.

---

## Medium Priority

### Slow playback button
Edge TTS supports rate adjustment via SSML (`-30%`). Add a "slow" button that regenerates the target phrase at ~70% speed. Learners need to hear the target multiple times at reduced speed — this is the most commonly requested feature in language learning apps.

### Prosody / rhythm feedback (text-based approximation)
French is syllable-timed; English is stress-timed. Infer rhythm errors from the transcription text without audio processing:
- If the transcription drops function words (`de`, `le`, `les`, `un`) while keeping content words, flag it with a note about even syllable timing
- Track which function words are consistently dropped across attempts — this is a reliable prosody signal derivable from text alone

### Minimal pairs drill
For specific phoneme errors identified by the confusion map, generate targeted minimal pairs (`vu/bu`, `son/sans`, `u/ou`) and drill them directly. Bridges the gap between knowing a sound is wrong and knowing what to do differently.

---

## Lower Priority / Long-term

### Marseille-specific phonology mode
Southern French has distinct features the learner is hearing daily but the tool currently treats as errors or ignores:
- Final `-e` is often pronounced (unlike Parisian French) — `une femme`, the final `e` is audible
- Nasal vowels are weaker or absent in some contexts
- Phrase-final intonation rises rather than falls

Regional flavor is currently only introduced at C1+ ("welcome"). Consider making it an explicit toggle earlier since that's the French the user is actually immersed in.
