# Pronoun section — feature reference & design log

The Pronouns section trains French object-pronoun use for a learner whose hardest problems are
**perception** (catching `le/la/les`, `lui/leur` in fast speech) and **production** (placing the
pronoun *before* the verb, and ordering `le` before `lui` — the reverse of English instinct).

Nav: sidebar **Pronouns** section (`sb-sec-pronouns`) → `#pronoun-hub`. Hub controls: CEFR **Level**,
**Focus** (natural-first order), and a **Mode** chip that picks one of four exercises.

## Research basis (why it's built this way)
- **Perception is the core gap.** In real speech clitics nearly vanish (`l'ai`, `i` for *il*, `j'le`).
  The fix is **Processing Instruction / structured input** — tasks whose meaning can't be recovered
  without decoding the pronoun (Écoute) or whose answer can't be picked without *hearing* it (Écouter & choisir).
- **Natural-first content.** The "sound native" levers — **dislocation** (`Marie, je la connais`),
  **on ≫ nous**, **clitic reduction** — are under-produced by learners, so they lead the focus list.
- **Difficulty hierarchy** (drives focus order): 3rd-person accusative `le/la/les` (gender + referent
  tracking) hardest; then `lui/leur`; then `y`/`en`; then clitic **ordering** (`me le`, `le lui`).
- **Production is rewired by retrieval**, not rules: say the natural form, scored, many short reps.

## The five modes
| Mode (hub chip) | `mode` id | Skill | Endpoint |
|---|---|---|---|
| **Écoute (listening)** | `listen` | Perception — decode the pronoun's referent | `/pronoun/listen` |
| **Choisissez et dites** | `choice` | Production — pick the right *word order*, say it | `/pronoun/placement` (`kind=choice`) |
| **Écouter & choisir** | `listen_choice` | Perception → production — hear it, pick which pronoun was said, then say it | `/pronoun/discriminate` |
| **À vous** | `scratch` | Production — produce the answer from scratch (placement) | `/pronoun/placement` (`kind=scratch`) |
| **Quel pronom ?** | `select` | Production — choose the *right* pronoun (le/lui/y/en) by verb rection, say it | `/pronoun/placement` (`kind=select`) |

Dispatch: `startPronoun()` → `startPronounListen()` or `startPronounDrill(kind)` in `static/index.html`.

### Quel pronom ? — `/pronoun/placement` (`kind=select`)
Trains **selection** (which pronoun), the accuracy gap the placement drills assume you've solved:
choosing `le/la/les` (COD) vs `lui/leur` (COI, à + person) vs `y` (à + thing/place) vs `en` (de + thing /
quantity). Produce-from-scratch like `scratch` (declarative `cue` → say the pronominalised `answer`,
Azure-scored), but the prompt (`_pronoun_drill_system` forces a **mix** regardless of the focus chip)
loads the verbs anglophones misjudge (*téléphoner/obéir/répondre à qqn* → lui; *regarder/attendre qqn* →
le; *penser à qch* → y; *parler/avoir besoin de qch* → en). **y/en are things-only** — never a person
(prompt-guarded). Each item carries a `note` (the rection rule, e.g. `téléphoner à qqn → lui (COI)`)
shown in the reveal box (`.pl-reveal-note`) on pass/give-up — the teaching payload.

### Écoute (listening) — `/pronoun/listen`
Reuses the **Dialogue-French comprehension runner** (`comprMode='dialogue'` + `comprIsPronoun=true`,
so back/new/try-again route to the pronoun hub). Generates a short spoken dialogue that features the
focus pronoun in reduced form, plus **referent-forcing** MCQs (impossible to answer without decoding
the pronoun; a wrong-referent distractor is present). Banked (`register="pronoun"`, `style=focus`),
Chirp3-HD audio via `_render_dialogue_lines` (cached to R2). Prompt: `_pronoun_listen_system(focus)`.

### Choisissez et dites / À vous — `/pronoun/placement`
One-scene drill of a single `kind`, **5 items**, each **said once → auto-scored → Next** (no
progressive stages, no mastery gate). `kind`:
- `choice` — cue + 3 options that differ **only in word order/placement** (distractors are the classic
  anglophone errors: pronoun after the verb, or reversed order). Say the correct one.
- `scratch` — cue + French instruction, answer hidden; produce it yourself.

**Answer gating (all drills):** the correct answer is revealed **only when the learner passes the spoken
attempt or explicitly gives up** (the "Show answer" button in `.sp-actions`). A missed attempt shows the
**score % only** — the word-level "heard" diff, per-word tips, the green option highlight and the "Correct
answer" box all stay hidden so a wrong try doesn't hand over the target. Learner retries the mic or clicks
Show answer. See `_placementRevealAnswer` / `_placementHideAnswer` / `placementGiveUp` in `static/index.html`.
- **Reveal form is per-mode:** for `choice` / `listen_choice` the reveal **just greens the correct on-screen
  option** — the separate "Correct answer" box (`#pl-reveal`) is suppressed as redundant. It renders **only
  for `scratch`**, where there are no visible options to highlight.
- **Answer predictability (`scratch` only):** in `À vous` the learner produces the answer blind and it's
  scored against ONE reference, so the cue must make the **entire** answer predictable — subject, verb, AND
  which words become pronouns. A cue that's an open question (`Qu'est-ce qu'il fait avec les cahiers ?`)
  leaves the **verb** free (`donne`/`passe`/`montre` all fit) → false fail. Fix: `scratch` cues are now a
  **complete declarative sentence** carrying the exact verb + full noun objects with an explicit (ideally
  pronoun) subject — e.g. `Il donne les cahiers à Clara.` — and the answer is that same sentence with only
  the objects pronominalised, same subject + verb: `Il les lui donne.` The frontend labels the scratch cue
  **"Phrase de départ"** (not "À dire"), since it's the source to transform. `choice`/`listen_choice` are
  immune (the answer is on screen / in the audio). Residual slips could still be caught by a
  subject/verb-tolerant scorer if needed.

Prompt builder `_pronoun_drill_system(kind, focus)` from `_PRONOUN_DRILL_INTRO` + `_PRONOUN_DRILL_KINDS`.
Backend drops any item with a blank `answer` and tags `stage=kind`; `choice` options are shuffled.

### Écouter & choisir — `/pronoun/discriminate`
Listening discrimination + production. **5 items**, one scene. Each item is a short **2-line exchange**
(setup + reply) rendered to audio via Chirp3-HD (cached to R2). The learner:
1. Hears the exchange (auto-plays; **Réécouter** replays).
2. Clicks **which reply was said** among 3 options differing **only in the object pronoun**
   (`le/la/les`, `lui/leur`, COD↔COI) — the ear decides, not word order. A **wrong click marks only that
   option red and lets them re-listen and try again** — the correct option is never green-highlighted on a
   miss. Only a correct pick (or Show answer) reveals it and unlocks the mic.
3. **Says it** (mic, auto-scored). Then Next.

Prompt: `_PRONOUN_DISCRIMINATE_SYSTEM`. Backend guarantees the correct line is among the options,
shuffles, sets `stage="listen_choice"`, `cue=setup`, and attaches `audio_url` per item.

## Focus taxonomy — `_PRONOUN_FOCI` (server.py)
Keys (natural-first): `dislocation`, `on_nous`, `reduction`, `dobj` (le/la/les), `iobj` (lui/leur),
`y_en`, `ordering`. Each entry = `(French label, listening directive, production directive)`, read via
`_pronoun_focus(focus)`. The three prompt families each pull the directive that fits their mode.

## Scoring — two engines
The placement mic uses **Azure Pronunciation Assessment when available, Web Speech + best-of-N as
fallback**. `startPronounDrill` calls `_ensureAzure()` (loads the Azure JS SDK + fetches a token from
`/azure/token`) and sets `_plAzureReady`. `togglePlacementMic` dispatches on that flag; both engines
converge on `_applyPlacementResult(data, item)` (render + gating + analytics), tagging `engine`.

### Azure engine (phoneme grading) — primary, **local-only**
- The browser SDK runs `recognizeOnceAsync` against the reference (`item.answer`) with a
  `PronunciationAssessmentConfig` (HundredMark, Phoneme granularity, `enableMiscue=true`). It grades the
  *sound* — so it separates `le`/`la`/`les`/`lui` by vowel, which transcription structurally cannot.
- Raw result JSON → **`/pronoun/azure/analyze`** → `_parse_azure_pa()` maps it onto the shared
  `ShadowAnalyzeResponse`: score = `PronScore/100`. Per-word **3-tier** (`_azure_word_tier`): green
  ≥ `_AZURE_WORD_GREEN` (80) · amber `_AZURE_WORD_AMBER`–79 (60, acceptable/polish, dotted underline) ·
  red < 60 or Insertion · grey = Omission. **Pass = PronScore ≥ `_AZURE_PASS_SCORE` (80) AND no
  red/omitted word** (amber is fine). "Words to work on" lists **only red/omitted** words, so a clean
  pass with an amber word doesn't nag — it just shows `Bien — un son ou deux à polir`. Red/omitted tips
  name the weakest phoneme. `WordResult.tier` carries the tier ("" for Web Speech results).
- **Local-only gate**: `/azure/token` (a) needs `AZURE_SPEECH_KEY`/`AZURE_SPEECH_REGION`, set only in the
  local `.env`, never Render, and (b) refuses non-`127.0.0.1` requests (403). So Azure runs only on the
  dev machine; **in production `_ensureAzure()` fails and the Web Speech path takes over automatically.**
  Free tier F0 (5 audio-hrs/mo); the local gate caps spend to your own testing. Test page: `azure-test.html`.

### Web Speech engine (fallback) — `/pronoun/speak/analyze`
Posts to **`/pronoun/speak/analyze`** → `_phrase_analyze(..., "pronoun_speak")` →
`score_attempt(..., phonetic=True)` — the exact pipeline the phrase exercise uses.
- **STT alternatives mining (clitic recovery)**: Web Speech mangles unstressed object clitics — the exact
  token these drills test (`les`→`est`, `le`→`leur`, dropped `y`). The placement mic requests
  `maxAlternatives=5` (desktop; 1 on mobile), builds a full-sentence candidate from each hypothesis
  (`_plAltCandidates`), and sends them as `alt_transcriptions`. `_phrase_analyze` scores the top guess +
  up to 6 alternatives and keeps the **best-matching** one, so a correctly-said pronoun isn't failed just
  because Chrome's #1 guess swapped in a commoner word. Opt-in per client: only the pronoun drills send
  `alt_transcriptions`, so other exercises are unchanged. **Residual limit**: if *all* hypotheses mangle
  the clitic, best-of-N can't recover it — which is exactly why the Azure engine above is preferred when
  present. The learner can still hit **Show answer** and move on.
- **Noun/adj tagging**: pronoun content isn't tagged at generation, so `/pronoun/speak/analyze` runs
  `tag_nouns_adjs` on the target when the client sends none — matching the phrase exercise's
  gender/number-aware scoring.
- **`leur` / `l'heure` fix**: the STT returns the homophone `l'heure` for a correctly-said `leur`.
  `_PHONETIC_HOMOPHONES` in `elision.py` (`lheure`/`lheures`/`leurs` → `leur`) is applied by
  `score_utils.normalize()` **only under `phonetic=True`** (speaking), so dictation still keeps them
  distinct. Keys are the post-normalization surface (apostrophes are stripped before this runs).
- **Result parity**: `_renderPlacementFeedback()` mirrors the phrase `renderFeedback()` — score % +
  animated bar + a "heard" row (what the STT returned, matched words snapped to target) + per-word
  tips via the reused `buildWordExercise` atom.

## Frontend map (static/index.html)
- Hub `#pronoun-hub` (mode/level/focus chips, `#pronoun-hub-content` shares the Atelier grid CSS).
- **Listening** reuses `#comprehension-view` via the `comprIsPronoun` flag.
- **Drills** (choice / scratch / listen_choice) share `#pronoun-placement-view` + one runner:
  `startPronounDrill`, `_renderPlacementItem`, auto-mic (`togglePlacementMic`, silence auto-submit like
  the phrase exercise), `submitPlacement`, `_renderPlacementFeedback`, `_placementPickOption` /
  `_placementReplayAudio` (listen_choice only), `_placementAdvance`, `_showPlacementSummary`.
- **One control row** (`.pl-controls`): `#pl-giveup-btn` (Show answer, left) · `#pl-mic-btn` (mic, centre) ·
  `#pl-advance-btn` (right). The right button is **"Skip" by default and flips to "Next →" / "Finish →"**
  once the item is solved (pass) or revealed (give-up) via `_placementSetAdvanceLabel()`; it always just
  calls `_placementAdvance`. Equal-flex side slots keep the mic centred. For `listen_choice` the mic is
  hidden (Skip / Show answer stay put) until a correct pick.
- Sidebar labels are CSS `::after` on `sb-nav-pronoun` / `sb-sec-pronouns`.

## Analytics events
`pronoun_listen_started` · `pronoun_placement_started` (carries `kind`) · `pronoun_placement_attempt`
(`kind`, `score`, `passed`, `engine` = `azure` | `webspeech`) · `pronoun_placement_giveup` (`kind` — learner hit Show answer) ·
`pronoun_placement_completed` (`avg_score`) · `pronoun_discriminate_started`
· `pronoun_discriminate_pick` (`correct`).

## Known gaps / refinements
- **Écouter & choisir determinism**: the setup line's full nouns can make the correct pronoun deducible
  by grammar rather than purely by ear (e.g. "ces roses à mamie" → `les lui`). Options to tighten:
  make line 1 leave the pronoun genuinely ambiguous, or hide the setup text (audio-only).
- **Dormant Reformulation mode**: `#pronoun-speak-view` + `startPronounSpeak`/`checkPronounWritten` and
  the routes `/pronoun/speak`, `/pronoun/speak/check` remain in the codebase but are **unreachable**
  (no hub chip). Kept for now; safe to delete for tidiness.
- **Banking**: only Écoute banks its dialogues (`content_bank`). Placement/discriminate generate fresh
  per session (audio still content-addressed → R2 cache hits on repeats); could be banked later.
- **Focus fit**: `dislocation` and `on_nous` are less about *placement*, so they suit Écoute/À vous more
  than the placement-style choice drill.

## History (chronological)
1. Built the hub: Écoute (referent-forcing listening) + Reformulation (transform) + a graduated
   Placement drill (rep → choice → scratch, 3-rep mastery gate).
2. Fixed the sidebar (own section + `::after` labels) and hub grid alignment.
3. Added the 3-accurate-reps mastery gate + `leur`/`l'heure` phonetic homophone fix.
4. Made the drill mic auto-stop on silence + auto-score (phrase-exercise parity), then brought the
   result render to full parity (% + bar + heard row + tips) and tagged noun/adj server-side.
5. **Restructure**: dropped the rep stage + progressive wrapper + Reformulation chip; split into flat
   modes — Écoute, Choisissez et dites, À vous.
6. Added **Écouter & choisir** (minimal-pair listening discrimination → pick → say).
7. **Answer gating**: stopped revealing the correct answer on a wrong attempt across all drills. Reveal now
   fires only on a pass or an explicit **Show answer** give-up; a miss shows the score % only, and a wrong
   `listen_choice` pick lets the learner re-listen instead of exposing the right option. New
   `pronoun_placement_giveup` analytics event.
8. **Consolidated the control row** to `[Show answer] · mic · [Skip→Next]` (`.pl-controls`); the right
   button flips Skip→Next on a pass/reveal.
9. **Azure Pronunciation Assessment (Phase 1)**: phoneme-level grading as the primary engine on the dev
   machine (`/pronoun/azure/analyze` + `_parse_azure_pa`), with the Web Speech + best-of-N path as the
   automatic fallback (and the only engine in production, where Azure is gated off). Grades the le/la/les/lui
   vowel that transcription can't hear. Spiked first with `azure-test.html` before wiring the drills.
   3-tier per-word colour green/amber/red; pass on green+amber, only red/omitted listed to "Words to work on".
10. **« Quel pronom ? » (`select`) mode**: a fifth mode training pronoun SELECTION by verb rection
    (le/lui/y/en) — the accuracy foundation the placement drills presuppose. Produce-from-scratch, mixes
    COD/COI/y/en, favours the English-mismatch verbs, and reveals the rection rule (`note`) as the teaching
    payload. Built as the first item of a broader intermediate pronoun-fluency plan (selection → communicative
    Q&A → speeded fluency).
