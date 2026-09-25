# French Tutor — Claude Session Instructions

## Testing responsibility
The user tests all frontend and browser behaviour directly in Chrome. Do not attempt Playwright, headless browser automation, or any automated UI verification. After implementing a frontend change, describe what to check and hand off — do not try to verify it yourself.

## Active frontend file — index.html
`static/index.html` is the live, active frontend (the vraiKronos rebuild). Do all frontend work there.
Design system files (still the source of truth for tokens/components): `static/vk-tokens.css`, `static/vk-components.css`, `static/vk-atelier-components.css`, `static/vk-theme-light.css`, `static/vk-theme-atelier.css`.
Staff pages (`static/admin.html` admin hub, `static/analytics.html` teacher dashboard) use the same Tonal style via `static/vk-atelier-staff.css`.

## Project purpose
A French language learning webapp built for a user living in Marseille who wants to improve listening and speaking. The tool uses Mistral AI (chosen for native French capability) and runs locally via FastAPI, accessed in Chrome.

## Stack
- **Backend**: FastAPI + uvicorn (hot-reload), Python 3.9
- **LLM**: Mistral AI — `mistral-large-latest` for tutoring, `mistral-small-latest` for routing and coherence checks
- **TTS**: Google Cloud **Chirp3-HD** (8 French voices) for the listening modes, cached to a Cloudflare R2 library (`library_store.py`); **edge-tts** (`fr-FR-DeniseNeural`) everywhere else and as the Chirp fallback
- **Speech input**: Web Speech API (browser-native, fr-FR, Chrome/Edge only)
- **RAG**: pypdf text extraction injected into system prompt from `/uploads/*.pdf`
- **Design system**: vraiKronos engine + Atelier product theme in the **Tonal** (Material 3) style — Hanken Grotesk (UI/body), Geist Mono (numbers + transcript), friendly blue `#2D6CB3`, tonal blue-tinted surfaces, pill buttons, rounded cards

## File map
| File | Purpose |
|---|---|
| `server.py` | FastAPI app — all routes, Mistral client, TTS generation (`generate_audio` via edge-tts, `generate_library_audio` via Chirp3-HD), Chirp voice pickers + `CHIRP_VOICE_NAMES`, custom-content + dictation + listen-answer + dialogue + vocab handlers |
| `library_store.py` | Chirp3-HD synthesis + content-addressed audio cache (`md5(voice|text).mp3`); Cloudflare R2 backend (shared local/prod) with local read-through cache, local-disk fallback; `synth_and_cache()`, `get_audio()` |
| `shadow_engine.py` | Single-phrase shadowing: `generate_phrase()`, `score_attempt()`, `analyze_mismatches()`; pulls liaison links via `detect_links` |
| `paragraph_engine.py` | Paragraph generation + per-chunk scoring: `generate_paragraph()`, `score_chunk()`, `analyze_mismatches()`, `analyze_patterns()`; `TOPICS` |
| `prosody_engine.py` | Sound-target / rhythm phrases: `generate_prosody_phrase()`, `analyze_prosody_mismatches()`, `annotate_phrase_rhythm()`; `SOUND_TARGETS` |
| `score_utils.py` | Shared scoring core (language-agnostic) — `normalize()`, `run_sequence_match()` (difflib SequenceMatcher), `build_display_results()`, `analyze_mismatches()`, `analyze_dictation_mismatches()`; each takes `lang` (default `"fr"`) and delegates language rules to `lang.get(lang)` |
| `azure_pa.py` | Azure Pronunciation Assessment helpers — `mint_token()` (browser token), `parse()` (result JSON → per-word green/amber/red/omitted tiers + weakest phoneme). Used by English Speaking via `/speaking/azure-token` (localhost or English beta) and `azure_json` on `/speaking/analyze`; F0 free tier, browser falls back to Web Speech on any Azure failure |
| `lang/` | Study-language profiles. `lang.get(code)` returns the profile; `SUPPORTED` lists codes. `LOCALES` maps the learner-facing locale (`fr-FR` / `en-US` / `en-GB`: voices, STT, content bank `bank/` / `bank-en-us/` / `bank-en-gb/`) to its language (`fr` / `en`: scoring); frontend language switch Français / English (beta only) plus a shared English accent setting US / UK / Both in the Speaking + Flashcards hubs (`enAccent`); `rollStudyLocale()` resolves it to `studyLocale` en-US/en-GB per item (phrase, paragraph, flashcard set), server `_resolve_locale()` + `_check_english_access()`. `lang/fr.py` = French: text/word normalization (wires `elision.py`), plural-s stripping, `tag_nouns_adjs`, `detect_links`, and the shadowing/dictation feedback prompts. `lang/en.py` = English (private beta behind `ENGLISH_BETA_USERS`): expands contractions, spells out numbers, UK→US spelling (curated map), speaking-only one-token homophones; tagging/liaison/plural-strip are no-ops; feedback prompts answer in French |
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
| `static/admin.html` | Super-admin hub (Overview, Users, Usage, Performance, Content pool) — standalone static file, fetches from `/admin/*` |
| `static/vk-atelier-staff.css` | Tonal overrides of core vraiKronos components (drawer, tabs, tables, tags, fields, dialogs, KPI tiles) + `st-*` layout classes for the two staff pages; scoped to `[data-theme="atelier"]`, not linked by the student app |
| `analytics.md` | Full analytics system reference — schema, event taxonomy, API endpoints, coach logic, known gaps |
| `vocabulary.md` | Vocabulary feature spec — Exposure + Recall (+ cumulative Review), `/vocab/generate` |
| `listening.md` | Listening feature reference & design log — the 2 modes (Listen & Answer, Dialogue French), Chirp3-HD + R2 cached library, random voices + French speaker names, shared `comprMode` runner, natural-pace-only decision, and why Real French/RFI was built then removed |
| `pronoun.md` | Pronoun section reference & design log — the 4 modes (Écoute, Choisissez et dites, Écouter & choisir, À vous), `/pronoun/*` routes, focus taxonomy, shared speaking scorer + `leur`/`l'heure` phonetic fix, known gaps |
| `future_updates.md` | Tech roadmap — updates deferred on a capability gap (e.g. STT upgrade → restore /r/, open/closed e, rhythm sound focuses) |
| `requirements.txt` | All dependencies |
| `.env` | `MISTRAL_API_KEY=...`; Chirp3-HD/library: `GOOGLE_TTS_API_KEY`, `R2_ENDPOINT`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`; Azure Pronunciation Assessment spike: `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` (also set in Render) |

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
`static/index.html` is a single-file app with M3 navigation — a **rail** on desktop (language FAB at top, avatar/account menu at bottom), a **bottom navigation bar** on phone (≤768px, "More" sheet beyond 5 destinations), and **area tabs** at the top of each hub page. All of it is driven by the `NAV_AREAS` config in the script (area → hub views); `navHubFor()` maps exercise views back to their hub. Trim `NAV_AREAS` to reshape the app. The navigation components' styles live in `vk-atelier-components.css` (APP NAVIGATION section); `index.html` only keeps their grid placement. It has a set of swappable views (`home`, `phrase`/`phrase-hub`, `paragraph`, `practice`, `comprehension-hub`/`comprehension`, `vocab-hub`/`vocab`, `custom`, …). It opens on `home`. Each exercise view shares the same control atoms:
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
Design system files live in `static/`. Token source of truth: `vk-tokens.css`. Components: `vk-components.css`. Themes: `vk-theme-light.css`, `vk-theme-atelier.css`. Exercise atoms: `vk-atelier-components.css`. Staff pages (admin + teacher): `vk-atelier-staff.css` — M3 navigation drawer (modal drawer + top bar on phone), `st-head`/`st-body` pages, `st-seg` segmented buttons, `.st-table-card` tables, `vk-overlay`/`vk-modal` dialogs, `.st-snackbar`. Account state (active / paused) uses tonal vs outline chips, never the performance colours.

**Always use `--vk-*` / `--pa-*` tokens directly — in page CSS, inline styles and JS-injected styles alike. The old `--k-*` / `--k35-*` colour bridge has been removed from `index.html`; do not reintroduce it. Never hardcode hex/rgba colours in a page — if a colour has no token, add one to `vk-theme-atelier.css`.**

The student app (`<body data-theme="atelier">`) uses the **Tonal** style (Material 3–inspired, adopted 2026-09). Tokens live in `vk-theme-atelier.css`; practice components in `vk-atelier-components.css`.
- **Separation by tone, not borders**: page `--vk-bg` (#F8F9FF) → cards `--vk-surface` (#EEF1FA) → nested panels inside a card `--vk-surface-lowest` (#FFF). Wells/hover `--vk-surface-2`. No borders or shadows on cards; dividers inside lists use `--vk-outline-variant`. Elevation only for the record FAB and floating UI (dropdowns, modals, toasts)
- **Shape**: buttons are pills (`--vk-btn-radius` 999px) · inputs/small controls 12px (`--vk-radius-ui`) · generic panels 16px (`--vk-radius-card`) · practice cards 24px (`--vk-card-radius`) · chips 8px
- **Selected / active**: `--vk-tonal` fill + `--vk-on-tonal` text (nav items, segmented buttons, toggle icon buttons, in-progress chips). No left-border active states
- **Hover / press**: an 8% / 12% overlay of the control's content colour via `color-mix(in srgb, <content> 8%, <bg>)` — not a separate hover colour per component
- **Type**: Hanken Grotesk for all UI + body, **sentence case** (`--vk-ui-case: none`), no letter-spaced uppercase labels. Sizes come only from the M3 type scale in `vk-theme-atelier.css` — `--vk-type-{display,headline,title,body,label}-{lg,md,sm}` (57/45/36 · 32/28/24 · 22/16/14 · 16/14/12 · 14/12/11); pick by role, never write px/rem sizes; 11px (`label-sm`) is the floor. Hero text that should scale with the viewport uses the fluid roles (both ends are M3 steps): `--vk-type-utterance` / `-utterance-hero` for the French target sentence, `--vk-type-fluid-{title,headline,display}` for big headings. All pages and DS files are migrated except `login.html`. Caps-mono labels become sentence-case Hanken; Geist Mono stays only on numbers. Data values shown as labels (error types, categories) go through `fmtLabel()` in `index.html`. Geist Mono only for numbers (scores, percentages, counters). French words — target sentence, heard/transcript row, word feedback, syllables — are always Hanken, never mono
- **Accent**: `--vk-accent` (primary fills, focus, recording) · `--vk-accent-text` (text buttons / accent text) · `--vk-accent-fg` (text on solid accent). Recording is always blue, never red
- **Performance colours** (`--pa-pass` #1F7A52 · `--pa-progress` #1A6B9A · `--pa-almost` #8A5A00 · `--pa-fail` #B0472E) are for scoring only, never for system state — use the tokens, never hardcoded hex. One exception: `.vk-btn-pass`, the pass-green filled button that appears only after a pass (phrase Next), carrying the verdict into the "go forward" action
- Red: `--vk-error` (destructive actions and system errors only). A wrong quiz answer or a failed score is scoring → `--pa-fail` / `--pa-fail-bg`; word diffs use `--pa-token-hit` / `--pa-token-miss`
- Category tags (question type, vocab register, dialogue speakers): `--vk-tag-*` — never borrow the performance colours for labels
- Floating UI: `--vk-elev-fab` (FABs, FAB-menu items), `--vk-elev-menu` (menus, popovers); backdrops `--vk-scrim` (sheets, trays, drawers) / `--vk-scrim-strong` (blocking modals)
- Progress & loading (M3): every bar is `.pa-lp` + `.pa-lp-fill` (4px, gap between fill and track, stop dot; `--pa-lp-h: 8px` for thick); rings are `.pa-cp` (JS sets `--pa-cp-pct` 0–1 on the `<svg>`); loading states use `.pa-loading` + `.pa-loader` (Expressive morphing shape) with `toggleLoading()` / `showLoadingError()` / `loadingHtml()`. Never hand-roll a track div
- Buttons: always `.vk-btn` + a variant — `-primary` (filled) · `-tonal` · `-outline` · `-text` · `-ghost` · `-destructive`; sizes `.sm` / default / `.lg` (48px hub start). The Tonal versions of these live in `vk-theme-atelier.css` and apply on every page; never hand-roll a page-level button. Icon-only secondary actions (close ✕, replay) are `.vk-icon-btn` (`.is-sm`, `.is-tonal`); play/record stay `pa-ctrl`. Single-select toggles are `.vk-seg` segmented buttons (`.is-block` to stretch). Filter/picker chips are `.vk-filter-chip` (selected: `.is-active`, `.active`, or a checked inner checkbox)
- Controls: play = tonal circle (`.pa-ctrl.is-outline`); record = 80px FAB with 24px corners that morphs to a circle while `.listening`; levels/speed = connected segmented buttons
- Motion: 100–150ms `cubic-bezier(0.4,0,0.2,1)` for colour/background; `--pa-ease-spring` for shape changes (mic, switch thumb, score landing)

## Accessibility — text contrast (WCAG AA)
On light surfaces, `--vk-fg-2` is the minimum for any readable text (in Tonal it is on-surface-variant `#44474F`, AA on every surface tone). `--vk-fg-3` and `--vk-fg-4` fail WCAG AA and must not be used on text elements. Full contrast table in `vraiKronos/design.md` under "Foreground token contrast — light theme".
