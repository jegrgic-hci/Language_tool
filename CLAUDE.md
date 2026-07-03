# French Tutor — Claude Session Instructions

## Testing responsibility
The user tests all frontend and browser behaviour directly in Chrome. Do not attempt Playwright, headless browser automation, or any automated UI verification. After implementing a frontend change, describe what to check and hand off — do not try to verify it yourself.

## Active frontend file — index.html
`static/index.html` is the live, active frontend (the vraiKronos rebuild). Do all frontend work there.
Design system files (still the source of truth for tokens/components): `static/vk-tokens.css`, `static/vk-components.css`, `static/vk-atelier-components.css`, `static/vk-theme-light.css`, `static/vk-theme-atelier.css`.

## Project purpose
A French language learning webapp built for a user living in Marseille who wants to improve listening and speaking. The tool uses Mistral AI (chosen for native French capability) and runs locally via FastAPI, accessed in Chrome.

## Stack
- **Backend**: FastAPI + uvicorn (hot-reload), Python 3.9
- **LLM**: Mistral AI — `mistral-large-latest` for tutoring, `mistral-small-latest` for routing and coherence checks
- **TTS**: Google Cloud **Chirp3-HD** (8 French voices) for the listening modes, cached to a Cloudflare R2 library (`library_store.py`); **edge-tts** (`fr-FR-DeniseNeural`) everywhere else and as the Chirp fallback
- **Speech input**: Web Speech API (browser-native, fr-FR, Chrome/Edge only)
- **RAG**: pypdf text extraction injected into system prompt from `/uploads/*.pdf`
- **Design system**: Kronos — IBM Plex Mono (UI), IBM Plex Sans (body), Impact (display), `#1A1A1A` sidebar, `#7A9393` teal, sharp corners, no border-radius

## File map
| File | Purpose |
|---|---|
| `server.py` | FastAPI app — all routes, Mistral client, TTS generation (`generate_audio` via edge-tts, `generate_library_audio` via Chirp3-HD), Chirp voice pickers + `CHIRP_VOICE_NAMES`, custom-content + dictation + listen-answer + dialogue + vocab handlers |
| `library_store.py` | Chirp3-HD synthesis + content-addressed audio cache (`md5(voice|text).mp3`); Cloudflare R2 backend (shared local/prod) with local read-through cache, local-disk fallback; `synth_and_cache()`, `get_audio()` |
| `shadow_engine.py` | Single-phrase shadowing: `generate_phrase()`, `score_attempt()`, `analyze_mismatches()`; pulls liaison links via `detect_links` |
| `paragraph_engine.py` | Paragraph generation + per-chunk scoring: `generate_paragraph()`, `score_chunk()`, `analyze_mismatches()`, `analyze_patterns()`; `TOPICS` |
| `prosody_engine.py` | Sound-target / rhythm phrases: `generate_prosody_phrase()`, `analyze_prosody_mismatches()`, `annotate_phrase_rhythm()`; `SOUND_TARGETS` |
| `score_utils.py` | Shared scoring core — `normalize()`, `run_sequence_match()` (difflib SequenceMatcher), `build_display_results()`, `analyze_mismatches()`, `analyze_dictation_mismatches()` |
| `elision.py` | French elision rules + homophones + number/gender-ending normalization — consumed by `score_utils.py` (and `analytics.py`) |
| `liaison_rules.py` | Mandatory liaison (‿) and enchaînement (⁀) detection: `detect_links()` |
| `pos_tagger.py` | spaCy `fr_core_news_sm` wrapper: `tag_nouns_adjs()`, `_get_nlp()` — feeds gender/number-aware scoring |
| `practice_list.py` | JSON-backed practice word list CRUD (stored under `data/`) |
| `document_engine.py` | PDF text extraction for uploaded docs, `UPLOADS_DIR` |
| `analytics.py` | SQLite event tracking, all aggregation functions, coach system |
| `phonetic_lookup.py` | Loads `data/Lexique383.tsv` once at import; `get_phonetic_categories(word)` → list of `nasal`/`u_sound`/`eu_sound` labels; consumed by `analytics.py` |
| `data/Lexique383.tsv` | Lexique383 French lexical database (25 MB, 142k rows) — `ortho` + `phon` columns used; downloaded from lexique.fr |
| `static/index.html` | Full single-file frontend (vraiKronos) — all student exercise views |
| `static/analytics.html` | Teacher dashboard — standalone static file, fetches from `/analytics/*` endpoints |
| `analytics.md` | Full analytics system reference — schema, event taxonomy, API endpoints, coach logic, known gaps |
| `vocabulary.md` | Vocabulary feature spec — Exposure + Recall (+ cumulative Review), `/vocab/generate` |
| `listening.md` | Listening feature reference & design log — the 2 modes (Listen & Answer, Dialogue French), Chirp3-HD + R2 cached library, random voices + French speaker names, shared `comprMode` runner, natural-pace-only decision, and why Real French/RFI was built then removed |
| `future_updates.md` | Tech roadmap — updates deferred on a capability gap (e.g. STT upgrade → restore /r/, open/closed e, rhythm sound focuses) |
| `requirements.txt` | All dependencies |
| `.env` | `MISTRAL_API_KEY=...`; Chirp3-HD/library: `GOOGLE_TTS_API_KEY`, `R2_ENDPOINT`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` (also set in Render) |

**Models**: `mistral-large-latest` (`_MODEL`) for content generation (paragraph, listen & answer, dictation); `mistral-small-latest` for the lighter calls (word-drill analysis, pronunciation tips, context phrases, vocab).

## Running the server
```bash
cd /Users/josephgrgic/Documents/GitHub/Language_tool
source .venv/bin/activate
python server.py
# visits http://127.0.0.1:8000
```

## Architecture
The app is an exercise platform, not a chatbot — there is no `/chat` route or conversational session state. Each exercise type is a generate → speak → score → explain loop. Most endpoints are stateless per request; persistence lives in `data/` (practice list), `user_content.json` (custom content), SQLite (analytics), and `uploads/` (PDFs). The shadow/prosody engines keep a small in-process `deque` of recent phrases to avoid repeats.

### Shared scoring pipeline (the heart of the app)
1. An engine generates the target French text via Mistral (or it comes from user/custom content)
2. Browser Web Speech API (fr-FR, Chrome/Edge) transcribes the user's spoken attempt; the frontend contracts elisions before sending
3. Frontend POSTs `{ target, transcription }` to the exercise's `…/analyze` route
4. `score_utils.normalize()` tokenizes both sides, applying elision/homophone/gender-ending normalization from `elision.py` and noun/adj tags from `pos_tagger.py`
5. `run_sequence_match()` (difflib `SequenceMatcher`) aligns tokens → per-word hit/miss
6. `build_display_results()` maps the result back onto the original tokens for display
7. `analyze_mismatches()` asks Mistral to explain the likely pronunciation issue per miss
8. `server.generate_audio()` renders the target with edge-tts for playback

### Exercise types (route families)
- **Shadow / phrase** — `/shadow/phrase`, `/shadow/analyze`, `/shadow/rhythm`: repeat a single generated phrase
- **Paragraph** — `/paragraph/start`, `/paragraph/analyze` (per chunk), `/paragraph/analyze-patterns`: read a paragraph chunk-by-chunk, then a cross-chunk pattern summary
- **Prosody** — `/prosody/targets`, `/prosody/phrase`, `/prosody/analyze`: phrases focused on a specific sound/rhythm target
- **Practice list** — `/practice-list` CRUD, `/practice-list/pronunciation`, `/practice-list/context-phrase`, `/analyze_word_drill`: user's saved words
- **Listen & Answer** — `/listen/generate`: passage + multiple-choice comprehension questions; audio via `/tts` with a random Chirp3-HD narrator (`voice: 'chirp-random'`)
- **Dialogue French** — `/natural/generate`: casual 2-speaker dialogue with named speakers (French names per voice), random mixed-gender Chirp3-HD pair, per-line cached audio + questions (shares the `comprMode` comprehension runner)
- **Dictation** — `/dictation/generate`, `/dictation/check`, `/dictation/check-inline`
- **Vocab** — `/vocab/generate`: Exposure + Recall flashcard session (spec in `vocabulary.md`)
- **Custom content** — `/custom/*`: user-supplied passages, persisted in `user_content.json`
- **Analytics / coach** — `/track`, `/analytics/*`, `/coach`: event logging + teacher dashboard (see `analytics.md`)

## Frontend features
`static/index.html` is a single-file app with a left nav and a set of swappable views (`home`, `phrase`/`phrase-hub`, `paragraph`, `practice`, `comprehension-hub`/`comprehension`, `vocab-hub`/`vocab`, `custom`, …). It opens on `home`. Each exercise view shares the same control atoms:
- **Play / pause** — `pa-ctrl-play`, plays the edge-tts audio of the target
- **Mic** — Web Speech API, fr-FR; the `…-mic-btn` toggles a `listening` class; works only in Chrome/Edge
- **Skip / Next / Continue** — `pv-func-skip` and the per-view advance buttons, laid out in the centered `.pv-func` controls row
- **Per-sentence / per-word scores** — colour-coded score bars rendered from the analyze response

## Known constraints
- **Python 3.9**: use `Optional[str]` from `typing`, NOT `str | None` union syntax — this will crash the server
- **edge-tts is async**: `generate_audio` must be `async def` and called with `await` inside FastAPI routes
- **`clean_for_tts()`** in `server.py` strips emoji, markdown (`**`, `*`, `_`), bullets, em-dashes before sending to TTS
- **Audio security**: filename validated with `re.fullmatch(r"[a-f0-9]{32}\.mp3", filename)`
- **Upload security**: filenames sanitized with `Path(filename).name`
- **spaCy model**: `pos_tagger.py` loads `fr_core_news_sm` — it must be installed (`python -m spacy download fr_core_news_sm`) or scoring that depends on noun/adj tagging will fail

## Completed work (elision scoring — single-token approach)
- **Problem**: elided words like `t'as`, `l'heure`, `j'ai` were being scored incorrectly. The old approach expanded elisions into 2 tokens for comparison (e.g. `j'ai` → `["je", "ai"]`), causing alignment failures and requiring a fragile dropout correction hack.
- **Fix**: elisions are now kept as single tokens end-to-end. `elision.py` holds the canonical rule list (`FRENCH_ELISION_RULES`) — it contracts expanded forms (e.g. `"je ai"` → `"j'ai"`) rather than expanding them. `score_utils.py` imports `normalize_french()` (and the homophone/gender helpers) from it, and the engines call into `score_utils`. The frontend `contractElisions()` in `static/index.html` mirrors the same rules in JS so the live transcript display and what gets sent to the backend are already in contracted form.
- **Scoring**: `_normalize()` no longer expands elisions. `_norm_parts()` and the dropout correction pass have been removed. `display_results` is now a clean 1:1 mapping between original tokens and normalized tokens.

## Hyphenated and underscore-linked words in scoring
Hyphenated compounds like `sous-estimé` and liaison-marked tokens like `Mes_enfants` are single visual tokens but the Web Speech API returns them as separate words. Current approach:
- `normalize()` in `score_utils.py` replaces both `-` and `_` with a space before stripping punctuation, so `Mes_enfants` → `["mes", "enfants"]` and `sous-estimé` → `["sous", "estimé"]` (2 scoring tokens each)
- The speech API output (`su estime`) also normalizes to 2 tokens, giving SequenceMatcher two near-miss pairs to score rather than one total mismatch
- `display_results` and `mismatches` are built from the merged view — consuming `len(_normalize(orig_token))` entries per original token — so the display still shows `sous-estimé` as one word
- **Known limitation**: `sous`/`su` is still an exact-token mismatch; phonetic proximity is not yet handled. SequenceMatcher gives partial credit for the `estimé`/`estime` pair but none for `sous`/`su`. A future fix could add `su` → `sous` to `FRENCH_HOMOPHONES` or introduce fuzzy/phonetic matching at the token level.

## Verb-ending homophone canonicalization (phonetic scoring)
French conjugations collide massively by sound, and the Web Speech API returns one arbitrary valid spelling of what it heard — so a correctly-pronounced verb was being scored wrong purely because the STT picked a different (valid) spelling than the target (e.g. target `parlez`, STT `parlé`).
- `canonicalize_verb_endings()` in `elision.py` rewrites homophonous verb endings to one canonical real form, applied symmetrically to target and transcription: `[e]` family (`-er/-é/-ée(s)/-és/-ez`) → `é`; `[ɛ]` family (`-ais/-ait/-aient`) → `ait`. Only truly identical sounds are merged (no score inflation). Present-tense `-e/-es/-ent` is deliberately NOT collapsed (`-ent` is pronounced in non-verbs like `vraiment`). Literary passé-simple `-ai` is excluded (it's `[ɛ]` in `vrai/mai/quai`).
- Guard: `_VERB_ENDING_EXCLUSIONS` holds `-er`/`-ers` words pronounced `[ɛʁ]` (`mer, fer, cher, hier, hiver, …`) whose canonical form would collide with a real `[e]` word (`fer`→`fé` vs `fée`→`fé`). Excluding a word is always safe (reverts to prior behaviour); extend the set as loanwords surface. A `len < 4` guard skips short function words.
- Gated by the `phonetic=True` flag on `score_utils.normalize()`. Speaking exercises (shadow/paragraph/prosody via `score_attempt`) pass `phonetic=True`; **dictation stays `phonetic=False`** so spelling still counts. This also removed the 10 hardcoded imparfait verbs from `FRENCH_HOMOPHONES` (the canonicalizer subsumes them, and keeping them in the always-on dict was a latent dictation false-positive).
- Display: the frontend "heard" rows snap matched words to the target surface form (`dr.word`) so the canonical `é` never shows on screen; true mismatches still show what was said.

## Updating elision rules
All elision rules live in **one place**: `elision.py` (`FRENCH_ELISION_RULES` list). To add or change a rule:
1. Add the `(pattern, replacement)` tuple to `FRENCH_ELISION_RULES` in `elision.py` — Python backend picks it up automatically via `normalize_french()`
2. Mirror the same rule in the `rules` array inside `contractElisions()` in `static/index.html` — this keeps the live transcript display consistent with backend scoring
- Rules are applied in order; put specific patterns (e.g. `je ai`) before their generic catch-all (e.g. `je + any vowel-word`)
- The `tu + avoir/être` colloquial contractions (`tu as` → `t'as`) are in section 6b — these are spoken French only and not standard written elisions

## Design rules (vraiKronos — current system)
Design system files live in `static/`. Token source of truth: `vk-tokens.css`. Components: `vk-components.css`. Themes: `vk-theme-light.css`, `vk-theme-atelier.css`. Exercise atoms: `vk-atelier-components.css`.

**Always use `--vk-*` tokens directly in new CSS. Never use `--k-*` or `--k35-*` bridge tokens — those exist only to support JS-injected styles and legacy code copied from `index.html`. Writing new CSS with bridge tokens hides the real token and breaks the contrast/colour rules below.**

- No border-radius anywhere
- No shadows on cards/buttons — borders only. Elevation reserved for floating UI (dropdowns, modals, toasts)
- Font families: `--vkg-font-mono` (IBM Plex Mono, UI labels) · `--vkg-font-sans` (IBM Plex Sans / Hanken Grotesk, body) · display: Anton/Impact
- **Accent**: `--vk-accent` (fills, active borders) · `--vk-accent-dim` (hover on solid fills) · `--vk-accent-text` (accent text on light bg) · `--vk-accent-fg` (text on solid accent fill)
- Red: `--vk-error` (destructive actions only)
- Score bars / status badges: green `#1F5A40` (≥70%) · amber `#8A5A00` (40–70%) · red `--vk-error` (<40%)
- All buttons: uppercase, letter-spacing, mono font; primary fill `--vk-accent`, hover `--vk-accent-dim`
- Active state: `--vk-accent` left border + `--vk-accent-bg` background tint
- Motion: 80–150ms `cubic-bezier(0.4,0,0.2,1)` — transitions on `color`, `background`, `border-color` only

## Accessibility — text contrast (WCAG AA)
On light surfaces, `--vk-fg-2` is the minimum for any readable text. `--vk-fg-3` and `--vk-fg-4` fail WCAG AA and must not be used on text elements. Full contrast table in `vraiKronos/design.md` under "Foreground token contrast — light theme".

The `--k-*` bridge tokens map directly to `--vk-*` — apply the same rule to them:
- `--k-text-primary` = `--vk-fg-1` ✓ safe
- `--k-text-secondary` = `--vk-fg-2` ✓ safe (minimum for readable text)
- `--k-text-muted` = `--vk-fg-3` ✗ **forbidden on readable text** — decorative/large-text only
