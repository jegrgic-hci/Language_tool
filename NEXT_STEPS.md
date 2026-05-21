# Next Steps

## 1. Fix elision scoring bug — `t'as`, `m'rend` false mismatches

**Root cause:** The normalization table maps `t'` → `te` universally, but in colloquial French `t'` before avoir/être forms means **tu**, not **te**.

- Target `"T'as"` normalizes to `["te", "as"]`
- Speech recognition outputs `"tu as"` → normalizes to `["tu", "as"]`
- `"te" ≠ "tu"` → scored as mismatch even though the user said it correctly

**Fix location:** `shadow_engine.py` and `paragraph_engine.py` — both have identical `_normalize()` and `_norm_parts()` functions with the same elision table.

**Approach:** Add a pre-substitution step before the generic elision regex that handles `t'` + avoir/être forms explicitly:
- `t'as` → `tu as`
- `t'es` → `tu es`
- `t'avais`, `t'avait`, `t'auras`, etc. → `tu ...`
- Pattern: `t'` followed by a conjugated form of avoir or être → expand as `tu`

Apply this in both `_normalize()` and `_norm_parts()`, before `_ELISION_RE.sub(...)` fires.

---

## 2. User-supplied content for listening and speaking practice

Allow the user to paste in their own sentences, paragraphs, or short stories and then practice on them — both listening (TTS playback) and speaking (shadowing/scoring).

**What this looks like:**
- A new input area (or modal) where the user pastes or types French text
- The tool TTS-plays it back sentence by sentence (or as a whole)
- The user can then shadow each sentence using the existing shadowing engine
- Works like the paragraph mode but with user-supplied content instead of AI-generated text

**Implementation ideas:**
- Add a new panel section or view: "Custom Content" or "My Text"
- On submit, split the text into sentences (by `.`, `!`, `?`)
- Store the sentence list in session state
- Reuse the existing paragraph view UI (`para-*` elements) to drive playback and scoring
- Backend: new `/custom-content` endpoint that accepts raw French text, returns sentence list + TTS audio for each
- Or: handle client-side sentence splitting and call existing `/tts` + `/score-chunk` endpoints per sentence

**Edge cases to think about:**
- Text may contain elisions, contractions, non-standard punctuation
- User might paste English by mistake — add a quick language check (mistral-small)
- Long texts: paginate or chunk into manageable practice units

---

# Pedagogy roadmap — listening & speaking methods

Core limitation these address: everything currently scores **imitation accuracy via ASR text**, which conflates speech-recognition error, pronunciation error, and not-knowing-the-word. Pure shadowing is also form-focused with no *meaning* load and no *perception* training — which is why imitation drills plateau. The items below add the missing **meaning** and **listening-discrimination** dimensions. Ordered by leverage (impact ÷ effort).

## 3. Multi-speed playback with progressive speed-up

**Why:** Highest-leverage missing feature; flagged 3× across existing docs. Slowed input is the most-requested feature in listening apps and unlocks a proven fluency drill.

**Method to support:** listen at ~70% → shadow at 70% → shadow at 100%. Also enables the **4/3/2 fluency technique** (same sentence, shrinking time budget).

**Implementation:**
- edge-tts supports rate adjustment via SSML / `--rate` (e.g. `-30%`)
- Add a speed control (e.g. 0.7× / 0.85× / 1.0×) on phrase, paragraph, and prosody cards
- Regenerate or cache TTS per (text, rate); cache by content hash since slow passes get replayed
- Touches `generate_audio()` in `server.py` and the audio controls in `static/index.html`

---

## 4. Close the spaced-repetition loop — session-start warm-up

**Why:** Spaced retrieval is the strongest retention lever available, and the data already exists — this is mostly a query. Removes the need for manual practice-list curation.

**Method:** surface the 3–5 lowest-accuracy words/phrases from recent sessions as a quick shadow warm-up when a session starts.

**Implementation:**
- `analytics.py` already stores per-word accuracy in SQLite — add a query for lowest-accuracy recent items
- New warm-up step before the main exercise; reuse the phrase shadowing UI and `score_attempt()`
- Feed missed items back as practice-list entries (integration point already half-wired)

---

## 5. Dictée (listen-and-type) mode

**Why:** Nearly free given alignment scoring already exists, and it's one of the strongest listening-comprehension exercises. Forces bottom-up parsing of liaison, elision, and schwa boundaries — exactly where the learner struggles.

**Method:** hear the sentence → type what was heard → diff against target.

**Implementation:**
- Reuse `score_chunk()` / `score_attempt()` for the text diff (no new scoring logic)
- New view + route mirroring the paragraph flow but with a text input instead of mic
- Reuse `display_results` word-level diff rendering for the correction view

---

## 6. Perception minimal-pairs (listening discrimination)

**Why:** The roadmap already lists *production* minimal pairs, but the listening bottleneck is *hearing* the contrast. This is the single biggest missing piece for listening comprehension.

**Method:** 2-alternative forced choice — play one of a minimal pair (e.g. `vu`/`vous`, `son`/`sans`, `dessus`/`dessous`), user picks which they heard. Driven by the analytics confusion map so it targets *their* weak phonemes.

**Implementation:**
- Source pairs from the phoneme-confusion table in `paragraph_engine.py` (`_PHONEME_CONFUSION_TABLE`)
- TTS each side of the pair; randomize which is played; track per-contrast accuracy in analytics
- No ASR needed — pure perception, so it isolates listening from production

---

## 7. Prosody/mumble pass before the word pass

**Why:** The research-backed shadowing progression is listening → silent shadowing (mouthing) → mumbling/prosody shadowing → full shadowing → content shadowing. The tool currently jumps straight to full shadowing. The prosody scaffold to support an earlier stage already exists.

**Method:** add a "melody first" stage — hum/mumble the contour, ignore words — before the word-accuracy pass.

**Implementation:**
- Insert as a stage in the paragraph/phrase progression (e.g. stage 0.5)
- Reuse the prosody card (syllable dots, rhythm groups, IPA) from `prosody_engine.py` as the visual scaffold
- No scoring on this pass, or score loosely on rhythm-group count only

---

## 8. Backchaining for hard sentences

**Why:** Long sentences lose their intonation contour when shadowed cold. Building from the end backward preserves the final-group melody — a classic drama/interpreter technique.

**Method:** `…jardin` → `…le jardin` → `…dans le jardin` → full sentence.

**Implementation:**
- Natural fit for the sentence drill tray (`#para-sent-drill-tray`)
- Generate progressive tail-fragments client-side from the target sentence tokens
- TTS per fragment (benefits from the speed/caching work in item 3)

---

## 9. Retell stage — shadowing → real speaking bridge

**Why:** Shadowing alone is imitation, not generation. A retell step converts imitation into production from meaning, which is what actually transfers to conversation.

**Method:** after shadowing a paragraph, hide it; user reproduces the gist in their own words; the tutor model grades *meaning*, not imitation.

**Implementation:**
- Add as a final paragraph stage after the whole-paragraph shadow
- Grade with the existing tutor infra (`tutor.py`, `mistral-large-latest`) — semantic similarity / key-idea coverage, not word match
- Distinct feedback style from shadowing (content coverage, not pronunciation tips)

---

## 10. Marseille input toggle

**Why:** The user is immersed in southern French daily, but the tool teaches standard French and treats southern features (audible final -e, weaker/absent nasals, rising phrase-final intonation) as errors — a direct mismatch with the learner's real listening environment. Regional flavor is currently only "welcome" at C1+.

**Method:** make regional flavor an explicit toggle available at all levels; at minimum, stop penalizing southern variants in scoring.

**Implementation:**
- Toggle passed into phrase/paragraph/prosody generation prompts to bias toward southern prosody markers
- Add southern variants to the homophone/normalization layer (`elision.py`) so audible final -e etc. don't score as mismatches
- Note regional pronunciation in feedback tips rather than flagging as an error
