# French Tutor — Claude Session Instructions

## Active frontend file — index.html
The vraiKronos rebuild is complete and `index-v3.html` has been copied into **`static/index.html`**, which is now the live, active frontend. Do all frontend work in `static/index.html`.
The older versions (`index-v3.html`, `index_v2.html`) are **sunset** — do not edit them; they are kept only for reference.
Design system files (still the source of truth for tokens/components): `static/vk-tokens.css`, `static/vk-components.css`, `static/vk-atelier-components.css`, `static/vk-theme-light.css`, `static/vk-theme-atelier.css`.

## Project purpose
A French language learning webapp built for a user living in Marseille who wants to improve listening and speaking. The tool uses Mistral AI (chosen for native French capability) and runs locally via FastAPI, accessed in Chrome.

## Stack
- **Backend**: FastAPI + uvicorn (hot-reload), Python 3.9
- **LLM**: Mistral AI — `mistral-large-latest` for tutoring, `mistral-small-latest` for routing and coherence checks
- **TTS**: edge-tts, voice `fr-FR-DeniseNeural`
- **Speech input**: Web Speech API (browser-native, fr-FR, Chrome/Edge only)
- **RAG**: pypdf text extraction injected into system prompt from `/uploads/*.pdf`
- **Design system**: Kronos — IBM Plex Mono (UI), IBM Plex Sans (body), Impact (display), `#1A1A1A` sidebar, `#7A9393` teal, sharp corners, no border-radius

## File map
| File | Purpose |
|---|---|
| `server.py` | FastAPI app, all routes, coherence check, TTS generation |
| `tutor.py` | System prompts and `get_response()` — calls mistral-large |
| `router.py` | Intent classification via mistral-small → `(mode, topic)` tuple |
| `document_engine.py` | PDF ingestion for RAG mode, `UPLOADS_DIR` |
| `audio_engine.py` | Legacy CLI audio (not used by webapp) |
| `elision.py` | French elision contraction rules — shared by `shadow_engine.py` and `paragraph_engine.py` |
| `static/index.html` | Full single-file frontend — Kronos two-panel layout |
| `static/analytics.html` | Teacher dashboard — standalone static file, fetches data from API endpoints |
| `analytics.py` | SQLite event tracking, all aggregation functions, coach system |
| `analytics.md` | Full analytics system reference — schema, event taxonomy, API endpoints, coach logic, known gaps |
| `requirements.txt` | All dependencies |
| `.env` | `MISTRAL_API_KEY=...` |

## Running the server
```bash
cd /Users/josephgrgic/Documents/GitHub/Language_tool
source .venv/bin/activate
python server.py
# visits http://127.0.0.1:8000
```

## Architecture
### Session flow
1. Browser generates a UUID session ID stored in `localStorage`
2. Each `/chat` POST carries `{ message, session_id }`
3. Server maintains in-memory `sessions` dict with `history`, `mode`, `topic`, `drill_state`
4. On mode change, history is wiped and fresh context is built

### `/chat` endpoint order of operations
1. If `drill_state.type == "number"` → validate the user's numeric answer, return result
2. `route(message)` → classify intent → set mode/topic
3. If new mode is DRILL + number topic → generate number sentence, set `drill_state`
4. `check_coherence(message, history)` → if incoherent, return clarification bubble
5. Call `get_response(history, mode, topic)` → main tutor reply
6. Generate audio via `generate_audio(reply)` → returns filename
7. Return `ChatResponse(reply, audio_url, mode, topic, drill_type)`

### Session modes
- `CHAT` — free conversation
- `SCENARIO` — roleplay as native French speaker in a real-world context
- `DRILL` — focused exercise (numbers, connectors, Marseille districts)
- `RAG` — lesson grounded only in uploaded PDF documents

## Frontend features
- **Two-panel layout**: dark left panel (controls) + light right panel (chat)
- **Listening mode**: blurs all tutor bubbles; click bubble or eye icon to reveal individually
- **Stop button**: red square on each tutor bubble, shows only while audio is playing
- **Mic button**: Web Speech API, fr-FR, toggles red pulsing state while recording
- **AbortController**: cancels in-flight fetch + pauses audio on stop
- **Clarification bubbles**: teal left border + italic style, `drill_type === 'clarification'`

## Known constraints
- **Python 3.9**: use `Optional[str]` from `typing`, NOT `str | None` union syntax — this will crash the server
- **edge-tts is async**: `generate_audio` must be `async def` and called with `await` inside FastAPI routes
- **`clean_for_tts()`** in `server.py` strips emoji, markdown (`**`, `*`, `_`), bullets, em-dashes before sending to TTS
- **Audio security**: filename validated with `re.fullmatch(r"[a-f0-9]{32}\.mp3", filename)`
- **Upload security**: filenames sanitized with `Path(filename).name`

## Pending / next features (as of last session)
- **Confidence threshold + phonetic check**: The user frequently mispronounces a key word, which speech recognition transcribes incorrectly, ruining the conversation flow. Planned approach:
  1. **Frontend**: capture `event.results[0][0].confidence` from Web Speech API; if below ~0.65, warn the user inline before sending (amber indicator, offer to retry or proceed)
  2. **Backend**: pass `confidence: float` in the chat request body; when confidence is low, use a more targeted coherence prompt that identifies the specific likely-mispronounced word and says something like "Le verbe 'X' semble incorrect — tu peux répéter ?" rather than a generic clarification

## Completed work (elision scoring — single-token approach)
- **Problem**: elided words like `t'as`, `l'heure`, `j'ai` were being scored incorrectly. The old approach expanded elisions into 2 tokens for comparison (e.g. `j'ai` → `["je", "ai"]`), causing alignment failures and requiring a fragile dropout correction hack.
- **Fix**: elisions are now kept as single tokens end-to-end. `elision.py` holds the canonical rule list (`FRENCH_ELISION_RULES`) — it contracts expanded forms (e.g. `"je ai"` → `"j'ai"`) rather than expanding them. Both `shadow_engine.py` and `paragraph_engine.py` import `normalize_french()` from it. The frontend `contractElisions()` in `static/index.html` mirrors the same rules in JS so the live transcript display and what gets sent to the backend are already in contracted form.
- **Scoring**: `_normalize()` no longer expands elisions. `_norm_parts()` and the dropout correction pass have been removed. `display_results` is now a clean 1:1 mapping between original tokens and normalized tokens.

## Hyphenated and underscore-linked words in scoring
Hyphenated compounds like `sous-estimé` and liaison-marked tokens like `Mes_enfants` are single visual tokens but the Web Speech API returns them as separate words. Current approach:
- `normalize()` in `score_utils.py` replaces both `-` and `_` with a space before stripping punctuation, so `Mes_enfants` → `["mes", "enfants"]` and `sous-estimé` → `["sous", "estimé"]` (2 scoring tokens each)
- The speech API output (`su estime`) also normalizes to 2 tokens, giving SequenceMatcher two near-miss pairs to score rather than one total mismatch
- `display_results` and `mismatches` are built from the merged view — consuming `len(_normalize(orig_token))` entries per original token — so the display still shows `sous-estimé` as one word
- **Known limitation**: `sous`/`su` is still an exact-token mismatch; phonetic proximity is not yet handled. SequenceMatcher gives partial credit for the `estimé`/`estime` pair but none for `sous`/`su`. A future fix could add `su` → `sous` to `FRENCH_HOMOPHONES` or introduce fuzzy/phonetic matching at the token level.

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
