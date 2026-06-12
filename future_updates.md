# Future Updates — Tech Roadmap

Planned technical upgrades and the features each one unblocks. Items here are **deferred by a
capability gap**, not just backlog — each is parked on a specific dependency. When the dependency
lands, do the linked follow-up work.

Single-purpose plans live in their own files and are referenced, not duplicated:
`plan_optimization.md` (Mistral cost reduction), `plan_public_launch.md` (subscription product),
`analytics.md` (analytics system + Progress-chart redesign).

---

## 1. STT upgrade → restore the un-assessable sounds

**Dependency:** phoneme-level pronunciation scoring (e.g. Azure Pronunciation Assessment), to
replace/augment the current word-level Web Speech API match. Tracked as the phonetic-scoring plan.

**Why parked:** our only scoring signal today is whether the STT transcribes the *correct word*.
That signal is **blind** to several important French sounds because mis-articulating them does not
change the transcribed word:

| Sound | Why word-match can't see it |
|---|---|
| **Uvular R /ʁ/** | An English/tapped r in `regarder` still transcribes `regarder` → counts as a hit |
| **Open vs closed e** /e/ vs /ɛ/ | Saying the `é` in `été` as `è` still transcribes `été` |
| **Rhythm & flow** (groupes rythmiques) | No word-level signal at all |

A pronunciation tool must only **offer a sound focus it can actually assess** — so these three were
removed from the live sound-focus chips (June 2026). Current live menu: **Any · Liaison · Nasal
Vowels · French /y/** (the set that *is* visible to word-match). See the
`project_sound_focus_insights` memory for the full kept/dropped rationale.

These sounds are genuinely important for French pronunciation — the R and the é/è distinction in
particular — so they should come back, but only once we can give honest feedback on them.

**Follow-up work when phoneme scoring lands:**
1. Re-add the chips to `#hub-sound-chips` in `static/index.html`: `r_sound`, `open_vowels`,
   `rhythm`. The backend definitions were **left parked, not pruned** — `SOUND_TARGETS` in
   `prosody_engine.py` and `_SOUND_FOCUS_DESCRIPTIONS` in `shadow_engine.py` still hold all three,
   so this is a frontend-only restore.
2. Wire the phoneme scorer so these focuses produce real per-sound feedback (the gap that made them
   hollow in the first place).
3. Extend the planned `get_sound_accuracy()` insight beyond liaison/nasal/y to include these once
   the underlying error is actually measurable (not just inferable from word-match).

---

## How to use this doc

When a dependency above is satisfied, do the listed follow-up work and move the entry to a
"Shipped" note (or delete it). Add new entries only when something is blocked on a capability we
don't yet have — not for ordinary backlog.
