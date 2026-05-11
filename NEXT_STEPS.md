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
