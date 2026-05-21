# Prosody Mode — Feature Notes

## Status: retired as a standalone section

The dedicated Prosody section has been removed from the sidebar. The core ideas that worked have been folded directly into the phrase shadowing exercises in the Generate section.

## What was incorporated

### Rhythm mapping (phrase card toggle)
A waveform button on the shadow phrase card toggles inline rhythm group annotations on the phrase text itself. When active, the plain phrase (`Je voudrais aller au marché`) becomes (`Je voudrais | aller | au marché`) — same words, teal `|` separators marking the groupes rythmiques. The annotation is fetched from `POST /shadow/rhythm` (`prosody_engine.annotate_phrase_rhythm`) and cached per phrase so toggling on/off is instant after the first load.

### Syllable breakdown (phrase card toggle)
A **SYL** button on the same toolbar adds a second line below the phrase text showing the syllabified form (`Je | vou·drais | al·ler | au | mar·ché`). Syllable dots `·` appear between syllables within each word. Shares the same `/shadow/rhythm` fetch cache as the rhythm toggle — both are free after the first API call.

### Sound focus selection (Generate hub)
The six prosody sound targets (Liaison, Nasal Vowels, French /y/, Uvular R, Open vs Closed, Rhythm & Flow) are available as an optional filter in the Generate hub. Selecting one biases `generate_phrase` to produce phrases that feature that phonetic pattern. Defaults to "Any" (no constraint).

## Why the standalone section was removed

- Scoring was word-level only — the Web Speech API returns text, not audio features, so there was no way to measure whether a liaison was actually realized, a nasal vowel was produced correctly, or rhythm group boundaries were respected. The visual scaffolding had pedagogical value but the scoring feedback was misleading.
- The full prosody card (syllabification + liaison arcs + enchaînement marks + IPA + per-word coloring) added complexity without meaningful signal beyond what the simpler rhythm/syllable toggles provide in phrase mode.

## Remaining files

| File | Role |
|---|---|
| `prosody_engine.py` | `annotate_phrase_rhythm()` used by `POST /shadow/rhythm`; `SOUND_TARGETS` dict used by the Generate hub sound focus selection |
| `server.py` | `POST /shadow/rhythm` — annotates an existing phrase with rhythm groups, syllabification, liaisons |

The full prosody phrase generation routes (`POST /prosody/phrase`, `POST /prosody/analyze`) and the `#prosody-view` frontend are removed.
