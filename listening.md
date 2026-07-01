# Listening — feature reference & design log

The listening side of the tool exists to close one specific gap: the user's comprehension is strong on **structured, clearly-articulated French** but breaks down on **natural French** (films, podcasts, real conversation). This document records the listening modes, how they work, and — just as importantly — the design decisions and the reasoning behind them, including approaches we built and later removed.

---

## The problem (why this exists)

Clean TTS (edge-tts `fr-FR-DeniseNeural`) produces studio speech: full articulation, every `ne`, standard register, one flat voice. Real French differs on several axes at once:

1. **Speed / débit** — faster, vowels reduced
2. **Reductions** — `je ne sais pas` → *chais pas*, `il y a` → *ya*, dropped `ne`, swallowed schwas
3. **Register & slang** — *ouais, ben, du coup, en fait, truc*
4. **Multiple speakers** — different voices, natural turn-taking
5. **Voice naturalness** — human-like prosody, intonation, rhythm

We split the work into **Gap A** (register / reductions — a *content-layer* problem, cheap) and **Gap B** (voice/acoustic realness — needs a better voice engine or real audio). Listening was chosen as the primary venue because it is **input-only**: the learner's voice never goes through STT, so reductions and fast speech carry **no scoring risk** (unlike the speaking exercises, where casual targets would wreck Web Speech API transcription).

---

## The voice engine — Chirp3-HD + a cached R2 library

The single biggest quality lever turned out to be the **voice engine**. edge-tts is serviceable but audibly synthetic; **Google Cloud TTS "Chirp3-HD"** voices sound markedly more natural in French. Chirp3-HD now powers **all phrase and passage audio across the app** — listening (Listen & Answer, Dialogue French), speaking/shadowing, paragraph shadowing, dictation, context phrases, and custom content. **Single-word pronunciation stays on edge-tts** (must stay maximally clear, and keeps a large finite word set off the Chirp budget). edge-tts also remains the universal **fallback** when Chirp isn't configured, a call fails, or the monthly budget is spent.

Because Chirp3-HD bills per character, everything is built around a **content-addressed cache** (any given text synthesized exactly once, ever) plus a **content bank** that reuses generated *text* across users and surfaces so the library compounds toward free.

### `library_store.py` — audio cache + budget guard
- **Cache key** = `md5(voice|text)` → `{hash}.mp3`. The 32-hex filename also satisfies the existing `/audio/{[a-f0-9]{32}\.mp3}` route validation, so library audio serves through the same route as edge-tts audio.
- **Backends**, chosen at import from env:
  - **Cloudflare R2** (S3-compatible, via `boto3`) when the `R2_*` vars are set → **one shared library across local dev and Render production** (no double-billing, no sync). The local `data/library/audio/` dir doubles as a **read-through cache** so a warm instance never re-fetches.
  - **Local disk** (`DATA_DIR/library/audio/`) otherwise → dev fallback.
- **`synth_and_cache(text, voice)`** — order: local cache → shared R2 → **budget check** → Google (the one billable moment); stores to both R2 and local on a miss; raises so the caller can fall back to edge-tts.
- **`get_audio(key)`** — serves the local cache first; in R2 mode fetches from the shared library on a miss and re-caches locally. The `/audio` route calls this when a filename isn't a local (edge) file.
- **`object_get/object_put(key, ...)`** — generic shared-store I/O (arbitrary keys, no `audio/` prefix), reused by the budget counter and the content bank.
- **Monthly budget guard** — a shared `{month, chars}` counter (`usage/chirp_chars.json`, R2 + local mirror, auto-reset each calendar month). `synth_and_cache` refuses **new** synthesis once the month's chars reach the **~900k soft cap** (headroom under the 1M free tier) and raises → caller falls back to edge-tts. Cache hits never count and always serve. `chars_used_this_month()` exposes the running total. This makes free-tier overage structurally impossible.
- Output is plain MP3, metadata is plain — **nothing is vendor-locked**, so the library stays portable.

### Cost & capacity model (why this stays free)
- Chirp3-HD free tier: **1M characters/month, resets monthly**; the guard caps *new* synthesis at ~900k. ≈ 150–165k French words. Overage is steep (**$30/1M**), so caching + banking is the whole game.
- **Audio on R2 is permanent**; only the monthly *generation* budget refills. So the library **compounds** — every month adds permanent pieces that are free forever after. R2 free tier is 10 GB with zero egress.
- Implication: cost is driven by generating *new unique text*, not by playback. The **content bank** (below) pushes ongoing cost toward zero by reusing banked phrases/passages across users and surfaces.

### `content_bank.py` — the self-seeding phrase/passage bank
The cache makes any given text free on replay; the **content bank** makes generated *text itself* reusable so the library grows deep enough that a learner rarely sees a duplicate — and generation (the only billable act) tapers toward zero.

- **Phrase is the atomic unit.** A `PHRASE` = one sentence + its single Chirp mp3 (content-addressed), tagged `{register, level, topic, style, voice, noun_adj_tokens, audio_hash}`. A `PASSAGE` = an ordered list of phrase ids sharing one narrator voice (so stitched playback sounds like one speaker), plus an optional lazy comprehension layer (`questions`, `vocab_preview`).
- **Cohesion lives in the text.** A paragraph is generated as coherent prose, then split into phrases; playback stitches the per-sentence clips gaplessly (preload-next). Cross-sentence prosody is not carried, but sentence boundaries reset intonation, so a well-authored paragraph reads naturally.
- **A paragraph is a seed.** Generating a speaking paragraph mass-produces tagged phrases that then feed the phrase, shadow, and dictation exercises. Phrases are pooled **style-agnostically** per `(register, level, topic)` for cross-pollination; passages keep `style` for coherence. Register `standard` (STT-safe) feeds shadow/paragraph/dictation/Listen & Answer; `casual` is Dialogue French.
- **Storage** mirrors `library_store`: JSON records + per-bucket index on the shared store (R2 + local read-through; local-disk fallback), so the bank is shared local↔prod and portable.
- **Novelty** is per-learner: `analytics.bank_seen (access_code, unit_id, ts)` records what each learner has been served, with a **last-seen timestamp** (upserted on repeat) that drives spaced recycle.

**Reuse-vs-generate policy** (`content_bank.select_for_user`, knobs at the top of `content_bank.py`) — the rule for a given `(learner, bucket)`:
- **Unseen pieces exist →** serve one (free cache hit). Exception: a small **freshness drip** (`DRIP_RATE`, ~8%) generates a fresh piece anyway *if* under the soft budget and the bucket is below `POOL_MAX` — so mature buckets keep evolving.
- **Learner has seen the whole bucket → spaced recycle:** replay their **least-recently-seen** piece *only if* the bucket is deep (`≥ POOL_TARGET`) **and** they last saw that piece `≥ RECYCLE_MIN_AGE_DAYS` ago. Otherwise **generate + bank** a new one (budget permitting). This means shallow buckets keep growing, deep buckets recycle instead of spending, and a repeat only comes back after a long gap.
- **Budget-aware:** generation of *new* bank content is gated by `library_store.generation_budget_ok()` (a **soft cap at 70%** of the monthly guard); above it we serve/recycle only, leaving headroom under the hard ~900k stop. If the budget is tight and a learner has exhausted a bucket, we recycle rather than spend.
- Net trajectory: **build organically early** (empty buckets → generate), **cost floors out as buckets mature** (reuse/recycle), with a slow freshness drip keeping content alive. Defaults: `POOL_TARGET=20`, `POOL_MAX=60`, `DRIP_RATE=0.08`, `RECYCLE_MIN_AGE_DAYS=30` — all tunable.

**Topic canonicalization** (`register_canonical_topics`, `_canon_topic`) — reuse only works when buckets collide, so topics are normalized (case/accent/spacing) and snapped to the app's canonical list (`paragraph_engine.TOPICS`, registered at server startup); unrecognized user subjects bucket by their normalized form. (Semantic matching of *differently-worded* same topics is a future upgrade.)
- **Voice policy:** random per piece, stable per piece (chosen once at bank time). Standalone phrase/paragraph/context/custom playback that isn't bucketed uses a fixed default narrator (`chirp-default` → Charon) so its text caches once.
- **Seeding:** `seed_bank.py` pre-generates core `level × topic × style` buckets offline (bounded by the budget guard) for day-one novelty; the long tail and user-added subjects fill organically at runtime.

**All surfaces are now bank-backed:**
- **Speaking** (paragraph → seeds phrases; shadow/speaking phrase; dictation) share the phrase pool as above.
- **Listen & Answer** keeps its **own longer-passage bucket** (`register="listen"`, kept separate from the speaking pool — at higher levels a longer passage is the challenge). It banks the passage **text + questions + a fixed voice**; playback stays lazy (`/tts` with that fixed voice) so reuse is a content-addressed cache hit.
- **Dialogue French** (`register="casual"`) banks the **lines + per-speaker voice + questions**; reuse re-renders the lines from the audio cache (no re-synthesis). Playback is unchanged (per-line auto-advancing playlist).
- Whole-text passages (listen/dialogue) store their text/structure in a `payload` on the passage record (empty `phrase_ids`); audio reuse comes from the cache, not stored hashes, so an edge-fallback render is never persisted as if it were Chirp.

### Environment
```
GOOGLE_TTS_API_KEY   = Google Cloud TTS API key (Text-to-Speech API enabled, billing on)
R2_ENDPOINT          = https://<account_id>.r2.cloudflarestorage.com
R2_BUCKET            = french-chirp3
R2_ACCESS_KEY_ID     = <R2 token access key id>
R2_SECRET_ACCESS_KEY = <R2 token secret>
```
Set in local `.env` and in Render's environment. `boto3` is in `requirements.txt`.

### Maximizing the free monthly allotment (`seed_bank.py`)
The 1M free chars **reset each month and unused characters are lost**, so late each cycle it pays to convert leftover budget into permanent banked audio.
- **`python seed_bank.py --status`** — shows chars used this month, remaining free allotment, room under the ~900k soft cap, and days until reset. Run this from an end-of-cycle **calendar reminder (~day 25)**.
- **`python seed_bank.py --per-bucket N`** — generates + banks `N` passages per core `level × topic × style` bucket (paragraph first, since it seeds the most phrases), bounded by the budget guard so it can't overspend. Requires `MISTRAL_API_KEY` + `GOOGLE_TTS_API_KEY` (+ `R2_*` to land in the shared prod library).
- `--dry-run` lists the buckets without generating; `--levels/--topics/--styles` scope the run.

---

## The two modes

Both share **one runner** — the comprehension view in `static/index.html` — selected by a `comprMode` flag (`'passage' | 'dialogue'`). The quiz, vocabulary preview, notes, and results screens are identical; only audio playback, transcript rendering, header, and routing branch. This keeps the quiz logic single-sourced.

| Mode | Hub | `comprMode` | Voice(s) | Register |
|---|---|---|---|---|
| **Listen & Answer** | `comprehension-hub` | `passage` | Chirp3-HD, 1 narrator (fixed per banked passage) | clean / standard |
| **Dialogue French** | `natural-hub` | `dialogue` | Chirp3-HD, mixed-gender pair (fixed per banked dialogue) | casual spoken (TTS-safe) |

### The 8 Chirp3-HD French voices
`fr-FR-Chirp3-HD-<name>`: female — **Aoede, Kore, Leda, Zephyr**; male — **Puck, Charon, Fenrir, Orus**.

Voices are **randomized for variety at generation, then fixed per banked piece** — so reuse always replays the same voice+text and stays a free cache hit:
- **Listen & Answer** — one random narrator (`pick_narrator_voice`) chosen when a passage is first generated and **stored on the banked passage**. `/listen/generate` returns that `voice`; the frontend sends it to `/tts` (not the old `chirp-random` sentinel), so a reused passage is a content-addressed cache hit.
- **Dialogue French** — one random **male + female** pair per dialogue (`pick_dialogue_voices`), order shuffled, fixed across the dialogue's lines and **stored per line** on the banked dialogue. Mixed-gender keeps the two speakers easy to tell apart.
- The `chirp-random` sentinel still exists for ad-hoc `/tts`, but the two listening modes now pass their banked voice; `chirp-default` (fixed narrator) is used for non-bucketed phrase/passage playback (context phrases, custom content).

### 1. Listen & Answer (`passage`)
Mistral writes a passage at a chosen CEFR level + topic; Chirp3-HD reads it; comprehension questions follow.
- Backend: `/listen/generate` (`_COMPREHENSION_SYSTEM`) — bank-backed via its own `register="listen"` longer-passage bucket (serve unseen / generate + bank per the reuse policy).
- Audio: single file, lazy-loaded via `/tts` with the passage's **banked voice** (returned as `voice`) so the passage shows instantly and reuse hits the cache.

### 2. Dialogue French (`dialogue`)
Casual two-speaker spoken dialogue — the "movie gap" addressed at the content layer. (Renamed from "Natural French".)
- Backend: `/natural/generate` (`_NATURAL_SYSTEM`) → JSON `lines: [{speaker, role, text, audio_url}]`. Drops `ne`, uses fillers (*ben / du coup / ouais / t'sais / grave*) and only **TTS-safe elisions** (`t'as`, `y a`, `j'ai`); avoids hard reductions (`chais pas`, `chuis`) the voice mispronounces. Bank-backed via `register="casual"` (serve unseen / generate + bank); on reuse the lines re-render from the audio cache (`_render_dialogue_lines`), no re-synthesis.
- **Speakers are named after their voices** with natural French first names (see mapping). The voice pair is chosen *first*, the names are injected into the Mistral prompt, so the names appear in **both the dialogue lines and the questions/options/explanations** (e.g. *"Pourquoi Julien est-il en retard ?"*). Internally each line also carries a stable `role` (`A`/`B`) for the coloured speaker badge and voice mapping, with a fallback to strict alternation if the model mislabels a line.
- Audio: each line rendered as its own cached mp3; the browser plays them **back-to-back as an auto-advancing playlist** (chained via `onended`, preloads the next line). No server-side stitching.
- Transcript: name-labelled lines with active-line highlight (`.dlg-line` / `.dlg-spk`, a pill sized for a full name).

### Voice → name mapping (`CHIRP_VOICE_NAMES`)
| Voice | Name | | Voice | Name |
|---|---|---|---|---|
| Aoede (F) | Chloé | | Puck (M) | Lucas |
| Kore (F) | Léa | | Charon (M) | Julien |
| Leda (F) | Manon | | Fenrir (M) | Thomas |
| Zephyr (F) | Inès | | Orus (M) | Hugo |

---

## Key design decisions (and why)

### Register over acoustics, content-layer first
Most of "movie French" is register and connectors, not extreme phonetic reduction. The cheapest, highest-value lever was generating casual *text* (Dialogue French) rather than chasing acoustic realism first.

### Dialogue must sound unscripted, not like a lesson
The naturalness of the *text* matters as much as the voice. `_NATURAL_SYSTEM` explicitly pushes an **overheard-conversation** feel, verified by generating samples and reading them:
- Vary turn length a lot — one-word reactions ("Ah ouais ?", "Mmh.", "Attends") mixed with rambling turns.
- Some turns just react/agree and add nothing new.
- Topic **drift** and named side-characters ("Martin en arrêt maladie", "Sophie a un deal avec le chef") make it feel lived-in.
- Light emotional colour (complaining, teasing, a sparing "haha").
- **TTS caveat:** false starts stay light and use a comma, **never `...`** — Chirp reads an ellipsis as a long dead pause. `clean_for_tts` currently maps `…` → `...`; if trailing ellipses ever sound like awkward gaps, normalize `…`/`...` to a comma in the Chirp path (flagged, not yet done).

### Natural pace only — no TTS rate manipulation
History: we first tried a "Fast" option via edge-tts `rate="+25%"` (mangled prosody), then via browser `playbackRate=1.25`. With the move to natural Chirp3-HD voices, **any speed manipulation was judged to work against the naturalness**, so the hub **Pace preset was removed entirely**:
- The TTS is **never** rate-manipulated server-side (Chirp/edge generate at natural pace; `generate_library_audio` passes no rate).
- Dialogue playback **always starts at natural ×1**.
- The in-player ×0.75 / ×1 / ×1.25 buttons remain as a **manual learner aid** (slow down to catch a word), defaulting to ×1 and resetting to ×1 on each new dialogue.
- **Do not reintroduce a hub speed preset or edge-tts `rate` for pacing.**

### Why "Dialogue French", named speakers
Two voices with generic "A"/"B" labels read like a worksheet. Naming the speakers (and threading the names through the questions) makes it feel like listening to two specific people. The names are drawn from the voice so a given voice is consistently the same "person" within a session.

---

## Removed: Real French / RFI (`authentic`) — and why

We built a third mode that streamed the real RFI **«Journal en français facile»** news podcast (real native audio + streamed MP3 + published transcript → subject-grouped questions), gated to a personal tier because RFI content is copyrighted.

**It was removed once Chirp3-HD landed.** Rationale:
- Chirp3-HD's naturalness is a **suitable replacement** for what RFI was reaching for (natural-sounding listening input) without the baggage: no copyright/licensing posture, no personal-tier gating, no dependency on RFI's feed/transcript HTML, no SSRF surface, works for all users, and it composes with our own level/topic/register control.
- Chirp3-HD is still **synthetic** — it doesn't reproduce true acoustic realness (background noise, overlap, off-mic). That specific slice of Gap B is now **deferred** rather than solved; if real-audio-with-noise is wanted later, a CC-licensed source (see below) is the redistribution-safe path, not RFI.

What was deleted: the entire `/authentic/*` backend section (feed fetch, `_TranscriptExtractor`, `_AUTHENTIC_Q_SYSTEM`, personal-tier helpers), the `personal_tier` flag on `/auth/me`, and all `authentic-hub` frontend (nav item, hub view, `startAuthentic`/`loadAuthenticEpisodes`, CSS, and every `comprMode === 'authentic'` branch). Old `authentic_listen_*` analytics rows remain in SQLite as harmless history; nothing reads them.

---

## Endpoint reference

| Endpoint | Method | Purpose |
|---|---|---|
| `/listen/generate` | POST | Passage + questions (Listen & Answer). Serves an unseen banked `listen` passage (own longer-passage bucket) or generates + banks; returns the passage's fixed `voice` for stable-cache lazy audio |
| `/natural/generate` | POST | 2-speaker casual dialogue (named speakers) + per-line cached audio + questions. Serves an unseen banked `casual` dialogue or generates + banks |
| `/tts` | POST | Audio for a piece of text. A Chirp voice name, `chirp-random` (random narrator), or `chirp-default` (fixed narrator, stable cache) routes to the cached Chirp3-HD library; anything else (single-word pronunciation) stays on free edge-tts |
| `/audio/{file}` | GET | Serves the mp3 — temp dir (edge) or the R2/local library (Chirp) transparently |

### Analytics events
- `listen_answer_started` / `comprehension_answered`
- `natural_listen_started` / `natural_listen_answered` (fields: `level`, `topic`) — the old `speed` field was dropped with the Pace preset.

---

## Implementation notes

- **Shared runner:** `comprMode` branches in `compToggleAudio`, `compReplayAudio`, `compConfirmBack`, `compTryAgain`, `_comprHubView`, `_comprShowResults`, and the nav active-state logic. `_comprLoadAudio` is guarded to `comprMode==='passage'`.
- **Voice pickers** (`server.py`): `pick_narrator_voice()`, `pick_dialogue_voices()` (male+female, shuffled), `voice_display_name()` (→ `CHIRP_VOICE_NAMES`), `_CHIRP_PREFIX`, `_CHIRP_RANDOM` sentinel.
- **Fallback safety:** `generate_library_audio()` uses Chirp3-HD when `library_store.chirp_enabled()` and the requested voice is a Chirp voice; on any exception it logs and falls back to edge-tts. With no Google key the whole listening stack silently runs on edge-tts.
- **Deploy dependency:** prod needs both the **code** (git push → Render pip-installs `boto3`) and the **5 env vars**. Env vars alone won't enable Chirp on prod.

---

## Known limitations & future ideas

- **Passage bank — built** (see `content_bank.py` above) and wired across **all** surfaces: paragraph/shadow/dictation (shared phrase pool), Listen & Answer (own `listen` bucket), and Dialogue French (`casual` bucket). Generate-once, bank, serve unseen banked pieces per learner (`bank_seen`), generate only on exhaustion. **Remaining:** an **export/backup endpoint** (zip the library for portability / disaster recovery).
- **Chirp is synthetic** — no true acoustic realness (noise, overlap). Deferred; a CC0/CC-BY source (e.g. Common Voice) is the redistribution-safe route if real audio is revisited.
- **Ellipsis pauses** — if trailing `…` read as awkward gaps in Chirp, normalize to a comma in the Chirp path.
- **boto3 + Python 3.9** — boto3 drops 3.9 support April 2026; a Python bump is on the roadmap.
- **Dictation register knob** — applying the casual-register idea to dictation is a possible Gap-A extension.
