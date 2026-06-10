# vraiFrench — index-v3.html Rebuild

**Goal:** Replace `index.html` (12,557 lines, 4,538-line inline CSS) with a clean `index-v3.html` built against the current design system. Incremental — one section at a time, verified before moving on.

---

## Why this rebuild

`index.html` accumulated several problems:
- **4,538 lines of inline CSS** — many of them re-declaring component styles already in the external design-system files
- **Legacy class names** mixed with new `vk-*` / `pa-*` / `pv-*` / `phv-*` class names
- **Dead resets** — blocks commented "replaced by X" that are no-ops but add noise
- Hard to audit, extend, or hand off

---

## Core principle — never copy CSS from index.html without checking the design system first

`index.html`'s inline CSS block is the problem this rebuild exists to fix — it is full of redundant re-declarations of things the design system already covers. **Do not treat it as the source of truth for how something should be styled.**

Before writing any CSS for a ported section:
1. Open the relevant design-system files (`vk-components.css`, `vk-atelier-components.css`, `vk-tokens.css`) and check whether a component class already exists for what you need.
2. If it does — use that class in the HTML. Do not copy the CSS into the inline block.
3. Only write inline CSS for what the design system genuinely cannot provide: app layout, view-specific positioning, ID overrides, and state bridges (e.g. `.vk-tab.active` when JS uses `.active` instead of `aria-selected`).

**Concretely:** buttons use `vk-btn vk-btn-primary/outline/ghost/destructive` + size modifiers (`sm`, `lg`, `icon`). Inputs use `vk-input`. Tabs use `vk-tabs` + `vk-tab`. Cards use `vk-card`. These are already styled — adding custom CSS on top creates the exact drift this rebuild is eliminating.

---

## Core principle — trust the design system

Before writing any CSS, check whether the design system already handles it. `vk-tokens.css`, `vk-components.css`, `vk-theme-light.css`, `vk-theme-atelier.css`, and `vk-atelier-components.css` cover tokens, components, themes, and exercise atoms. The inline `<style>` block in `index-v3.html` should only contain what those files genuinely cannot provide.

**Theme scoping:**
- `<html data-mode="light">` — activates light theme tokens everywhere (warm sand sidebar bg, borders, elevation, etc.)
- `<body data-theme="atelier">` — applies atelier fonts (Hanken Grotesk), blue accent, and warm paper to the entire app including the sidebar
- The sidebar gets its light sand background (`--vk-sidebar: #eae7e1`) from the light theme on `<html>` — atelier does not override that token, so the two layers compose correctly without any extra CSS
- Do NOT re-declare design-system tokens on `#sidebar` or any other element — that is exactly the redundancy this rebuild exists to eliminate.

---

## Core principle — CSS owns all visible content

To eliminate fragmentation between HTML and CSS, **all visible text labels live in CSS via `content:` on pseudo-elements**. The HTML carries only structure and semantic class names — no text nodes for labels, section headers, nav items, or brand strings.

```css
/* CSS owns the label */
.sb-nav-exercises::after { content: "Exercises"; }

/* HTML carries only structure */
<button class="vk-sb-item sb-nav-exercises" data-view="phrase-hub">
  <svg class="icon">…</svg>
</button>
```

**Why:** Changing a label, renaming a section, or reordering nav items requires a single CSS edit — no HTML touch, no risk of the two drifting out of sync.

**Applies to:** sidebar nav labels, section headers, brand wordmark/byline, footer buttons, any UI string that is presentational rather than semantic content.

**Exception:** dynamic values set by JS (timers, mode chips, scores) stay as text nodes or `textContent` targets since JS drives them at runtime.

---

The external design-system files already define all the component patterns:

| File | Coverage |
|---|---|
| `vk-tokens.css` | CSS variable tokens |
| `vk-components.css` | Generic `vk-*` UI components |
| `vk-theme-light.css` / `vk-theme-atelier.css` | Light + Atelier themes |
| `vk-atelier-components.css` | All `pa-*` / `pv-*` / `phv-*` exercise components + view layout shell |

The inline CSS in `index-v3.html` should contain only:
1. Token bridge — maps legacy `--k-*` / `--k35-*` tokens → `--vk-*`
2. App layout — `#app` grid, `#sidebar`, `#main`, mobile drawer
3. App-specific view styles — gate screen, chat, para/prosody/vocab chrome, drill tray, etc.

Target: **~1,800–2,000 lines** (down from 4,538). The ~295 lines of exact `pv-*`/`phv-*` duplicates are removed; everything else is retained until verified replaced.

---

## File locations

| File | Purpose |
|---|---|
| `static/index.html` | Current production file (do not touch during rebuild) |
| `static/index-v3.html` | New clean file (work in progress) |
| `static/vk-atelier-components.css` | Component source of truth for exercise views |

---

## JS approach

All JS is **lifted verbatim** from `index.html` throughout the build. The JS references elements by `id` (not class), so HTML class-name updates don't break behaviour. No JS changes until the HTML is fully verified.

JS source locations in `index.html`:
- Script block 1: lines 6208–11428
- Drill tray HTML (between scripts): lines 11429–11446
- Script block 2: lines 11447–12557

---

## Build steps

### ✅ Step 0 — Skeleton
**Status:** Complete

Created `index-v3.html` with:
- Clean `<head>` (5 external CSS links + Google Fonts)
- Token bridge + app shell CSS inline (~230 lines)
- Empty `#app` grid: `<aside id="sidebar">` + `<main id="main">`
- No JS yet

---

### ✅ Step 1 — Sidebar
Ported sidebar HTML using `vk-sb-*` classes from `vk-components.css`. Inline CSS covers brand block, session widget, nav item labels, collapse button, and footer.

Design decisions made during this step:
- **Brand block**: `Icon_language.png` (48px) + two-tone wordmark (`vrai` in accent blue / `French` in dark) via `::before`/`::after` on `.wm` span; `by vraifactors` byline below via `.version::after`
- **Active nav border**: widened to 3px (override of 2px system default)
- **Collapse button**: moved from footer into session widget header, inline with "SESSION" label; collapsed state shows chevron only
- **Session widget**: flex column with header row (label + chevron) above the `00:00` timer
- **`sb-mode-chip` removed**: not needed in this layout
- **CSS layer note**: sidebar design changes live in the inline `<style>` block; the correct long-term home for component-level changes is `vk-components.css`, but that update belongs in a separate design system project

**Verify:** Sidebar renders, collapse toggle works, mobile drawer opens/closes.

---

### ✅ Step 2 — Gate screen
Port `#gate` HTML. Copy gate CSS block (~200 lines) into inline `<style>`.

Design decisions made during this step:
- **`#gate-error` → `#welcome-error`**: the original CSS selector `#gate-error` was dead (HTML always used `id="welcome-error"`); v3 uses the correct ID consistently.

**Verify:** Gate shows on load, access code validates, app unlocks.

---

### 🚫 Step 3 — Chat view — DROPPED
Chat is not being ported to index-v3.html. The view is intentionally absent from the new build.

---

### ✅ Step 4 — Phrase view
Ported `#phrase-view` and `#phrase-hub` HTML. All `pv-*`, `phv-*`, `pa-*` component styles come from `vk-atelier-components.css`. Inline CSS covers only app-specific layout, ID resets, and hub styles.

Design decisions made during this step:
- **`.pv-speed-btn.active` bridge**: JS toggles class `active`, but external CSS only has `.pv-speed-btn.is-active` — added the bridge rule in inline CSS
- **`.sv-header`**: shared header strip (reused by prosody, and other views without their own pv-header)
- **JS deferred**: phrase JS lifted in Step 9 with the full script block

#### pv-card structure (phrase exercise)
The utterance card (`id="phv-card"`) contains, in order:
1. `.phv-toolbar` — save · SYL · rhythm · blur (replaces generic `pv-card-top`)
2. `.pa-utterance` — phrase text
3. `.phv-syllable-line` — hidden until SYL active
4. `#phv-score-block.phv-score-block` — score bar + `pa-transcript` diff; hidden until after an attempt; **inside the card**
5. `.phv-limit-msg` — attempts-exhausted note
6. `.phv-attempt-row` — pip row (left) + highest accuracy (right); always visible; **last child / card footer**

`#phv-word-list` (`pa-word-list`) sits **outside the card**, below the `pv-func` controls row. JS populates it with `pa-word-item` elements after scoring; hidden when empty.

#### ID naming convention
All phrase-view IDs use the `phv-` prefix to match the atelier component namespace:
- `id="phv-card"` (was `phrase-card`)
- `id="phv-score-block"` (was `phrase-feedback`)
- `id="phv-word-list"` (new — replaces the `.feedback-items` div that was inside the score block)

#### Action button classes
- Practice mic button: `pv-ss-drill-btn word-check-mic` — matches Drill button style; `word-check-mic` retained as JS state hook
- Drill button: `pv-ss-drill-btn`
- Save button: `phv-save-btn`; saved state → `is-saved` (not `added`)
- "Parfait" pass message: `pv-pass-label`

#### Hub layout
`#phrase-hub` uses a **two-column definition layout**: left column (180px) holds the row label + short description; right column (1fr) holds the chips or mode cards. Implemented with CSS grid on `#hub-content` and `display: contents` on `.hub-row` wrappers so each pair of cells participates directly in the parent grid. Row dividers are `border-top: 1px solid var(--vk-border)` on both cells, suppressed on the first row via `.hub-row:first-child > *`.

Row structure:
- **Level** — "Choose your difficulty"
- **Style** — "The type of content you'll practise with" · "Paragraphs only" is a plain left-aligned `.hub-style-divider-label` text node above the paragraph-only chips; no divider lines
- **Sound Focus** — "Target a specific phoneme" · `Any` chip communicates optionality, no "optional" label needed
- **Subject** — "What the exercise is about" · custom input sits inline in the right column below the chips
- **Format** — "How you'll practise" · mode cards + Start button

Token rule: all hub CSS uses `--vk-*` and `--vkg-*` tokens directly — no `--k-*` bridge tokens.

Additional design decisions:
- **Score bar threshold**: single tick at 90% only (`at-90`) — 90% is the pass goal
- **Demo button**: "Demo result" button in the phrase view header calls `demoPhraseFeedback()` — sets a demo phrase, fires `renderFeedback` with 80% score and two missed words (`enfants`/`leurs`) so the result UI can be verified without a backend connection. Remove when the view is fully wired.
- **Animations deferred**: score bar fill, `pa-score-land` number animation, and staggered `pa-word-item` delays are handled in Step 8

**Verify:** Phrase exercise loads, record → score → pip flow, word list appears below controls, auto-advance, coach banner.

---

### ✅ Step 5 — Paragraph view
Port `#paragraph-view` HTML.
Para chrome CSS (lines 2028–2870 in `index.html` inline block) is app-specific — copy it in.
`pv-*` / `pa-*` sentence score components use external file.
Add paragraph JS verbatim.

**Verify:** Paragraph loads, sentence-level scoring, drill tray slides in from right.

#### Paragraph exercise — state machine and controls (decisions made post-port)

**`setParaState` state machine:** all control visibility is driven by a single `setParaState(state)` call. States: `context` · `idle` · `listening` · `listening-retry` · `feedback-pass` · `feedback-fail` · `complete`.

**Context stage (initial full-paragraph listen):**
- Mic button is `display:none` — only play and skip are shown during the context listen.
- After audio ends, `advanceToNextStage()` fires automatically.

**Per-chunk controls:**
- Both play and mic are enabled immediately when chunk audio starts — the user can pause mid-playback and click mic at any time.
- `feedback-pass` keeps mic enabled so the user can re-record to improve their score; the continue button is shown alongside the mic.
- Continue button uses `vk-btn vk-btn-outline-accent sm` (class set dynamically by `setParaState('feedback-pass')`; all other states restore `pv-func-skip`).

**Score ring — replaced with `vk-ring` component:**
The original SVG approach (`stroke-dashoffset` attribute animation via RAF / CSS transition) was unreliable when the element transitioned from `display:none` for the first time — the browser had no "from" paint state for the CSS transition to animate from.

Replaced with the design system's `vk-ring-chart` component from `vk-components.css`:
```html
<div class="vk-ring-chart" style="width:96px;height:96px;">
  <svg class="vk-ring-svg" viewBox="0 0 120 120" width="96" height="96" aria-hidden="true">
    <circle class="vk-ring-track" cx="60" cy="60" r="50"/>
    <circle class="vk-ring-seg solo" id="para-score-ring-fill" cx="60" cy="60" r="50"
            style="--pct:0;--offset:0;"/>
  </svg>
  <div class="vk-ring-center">
    <span class="vk-ring-total" id="para-score"></span>
  </div>
</div>
```
Score is updated by `el.style.setProperty('--pct', pct / 100)` — CSS `calc()` inside `.vk-ring-seg` handles `stroke-dasharray` and `stroke-dashoffset` automatically. Transition is built into `.vk-ring-seg` in `vk-components.css`. Score-grade colours (`is-pass`, `is-almost`, `is-fail`) defined in `vk-atelier-components.css` as `.vk-ring-seg.is-*` overrides on the stroke.

**`recognition.onend` / `recognition.onerror` null guard:**
`micBtn` (the chat-view mic button, `document.getElementById('mic-btn')`) is `null` in the paragraph view. Both handlers now use `micBtn?.classList.remove(...)` with optional chaining to avoid a `Cannot read properties of null` crash.

#### Para viz — entry animations

`#para-viz` and `#para-sentence-scores` both have `animation: pa-rise … both` in the inline CSS. Because `display:none → display:flex/block` re-inserts the element into the render tree, the keyframe restarts automatically — no JS needed. Individual `.pv-ss-row` rows already had staggered `pa-rise` in the design system.

#### Para viz — ring and bar grow animation

The `vk-ring-seg` already transitions `stroke-dasharray` (built into `vk-components.css`). The `.pv-viz-p-fill` already transitions `width` (built into `vk-atelier-components.css`). Neither animated on first appearance because `setParaState` (shows viz) and `updateParaScoreBar` / `updateParaVizPhrases` (sets values) ran in the same synchronous JS task — no browser paint between them.

Fix — two `requestAnimationFrame` delays:
- In `renderParaFeedback`: `setParaState('feedback-*')` runs synchronously, then `requestAnimationFrame(() => updateParaScoreBar(pct, passed))` — ring paints at `--pct:0` first, then grows.
- In `showParagraphSummary`: `setParaState('complete')` first, then `requestAnimationFrame(() => updateParaScoreBar(...))`.
- In `updateParaVizPhrases`: phrase bars injected at `style="width:0%" data-pct="N"`, then rAF sets `el.style.width = el.dataset.pct + '%'` — bars grow in after paint.

#### Para viz — retry behaviour (viz persists across attempts)

When the user presses mic after seeing a result, the viz stays visible and animates back to zero instead of disappearing.

State routing on mic press: `setParaState(prevState === 'feedback-fail' ? 'listening-retry' : 'listening')`.

Changes to `setParaState`:
- `listening` — if `viz.style.display === 'flex'` (i.e. retrying from `feedback-pass`), call `paraVizResetAnimated()` and keep viz visible; otherwise hide as before.
- `listening-retry` — always keep viz visible, always call `paraVizResetAnimated()`.

`paraVizResetAnimated()`: sets `--pct: 0` on the ring fill (CSS transition animates the arc back to zero) and sets `width: 0%` on all `.pv-viz-p-fill` bars (CSS transition collapses them). Score text is cleared. Grade colour class is left in place so the ring drains in the previous colour before the new result arrives.

#### `paraAnalysisPending` flag — race fix

`submitTranscript` calls `recognition.stop()` then `handleParaResult(text)` (async fetch). The `recognition.onend` event fires before the `/paragraph/analyze` fetch resolves — at that point `dataset.state` is still `'listening'`, so both `onend` and `onerror` handlers were calling `setParaState('idle')` and hiding the viz mid-flight.

Fix: `paraAnalysisPending` flag is set to `true` before the fetch and cleared to `false` in both the success and error paths. Both `onend` and `onerror` skip the idle state reset while the flag is set.

#### Chunk transition — `.para-chunk-exit` fade

When the user presses "continue →", `handleParaAction` immediately adds `.para-chunk-exit` (`opacity:0; pointer-events:none; transition:opacity 200ms`) to `#para-viz`, `#para-tray-results`, and `#para-action-btn`. The TTS fetch for the new chunk typically takes 300–600ms, so by the time `setParaState('idle')` fires and hides those elements with `display:none`, they are already fully transparent — the snap is imperceptible. The class is removed in the `idle` branch of `setParaState` so it doesn't block the entry animation on the next result.

---

### ⬜ Step 6 — Remaining views (one at a time)

Prosody view dropped — dead feature, not ported.

Completed:
- ✅ **My Content** (`#custom-view`) — CSS converted to `--vk-*` tokens; action buttons use `vk-btn` system classes; JS and nav item were already present.
- ✅ **Listen & Answer** (`#comprehension-hub` + `#comprehension-view`) — hub with Level/Paragraphs/Topic rows; full view with persistent player, transcript/vocab/notes accordions, quiz flow, results + answer review. Level desc split: `#comp-level-desc` (qualitative) under Level row, `#comp-para-desc` (word count) under Paragraphs row. JS was already present; backend `/comprehension/generate` already in `server.py`.
- ✅ **Vocabulary** (`#vocab-hub` + `#vocab-view`) — hub with Level (CEFR chips, B1 default), Subject (10 chips + custom input, capped at 320px wide), and Words (5 / 10 / 15 / 20 / Cumulative chips) rows; card view with round stepper, card layout, word chips, definition-listen buttons. Transition screens removed — Exposure flows directly into Recall, and session end returns to the vocab hub. JS was already present; backend `/vocab/generate` already in `server.py`.

  **Round structure (updated):** 2 rounds — Exposure → Recall. Recall builds a shuffled deck of every word twice: once as **Listen & Identify** (hear definition → pick word chip) and once as **Read & Find** (see word → hear 4 definitions → pick the right one). Replaces the old 4-round Exposure / Path A / Path B / Quiz sequence.

  **Cumulative mode:** selecting the Cumulative chip generates 20 words upfront and processes them in 4 batches of 5. Batch 1 runs the normal Exposure → Recall cycle. Batches 2–4 run Exposure → Recall → **Review**, where Review is a recall-only pass over all accumulated words (2 cards per word, shuffled); wrong answers are pushed back to the end of the queue and repeat until the user gets 100%. A third **Review** pip appears in the round stepper only in cumulative mode. Key state: `_vocabIsCumulative`, `_vocabAllGenerated`, `_vocabBatchIdx`, `_vocabCumulativePool`, `_vocabInCumReview`, `_vocabIsLastBatch`.

  **Stepper redesign (done):** compact centered stepper replacing the old full-width tab bar.
  - `.vocab-part-item` is now `width: 100px` (not `flex: 1`) — steps are centered in the header strip via `justify-content: center` on `#vocab-parts`
  - Horizontal connector line drawn by `#vp-0::after` (always shown) and `#vocab-parts.is-cumulative #vp-1::after` (3-step mode only). Line runs from circle right-edge to next circle left-edge (`left: calc(50% + 11px); width: calc(100% - 22px)`)
  - `.vocab-step-dot` gets `position: relative; z-index: 1` so circles sit on top of the connector
  - `is-cumulative` class toggled on `#vocab-parts` by the existing `_vocabIsCumulative` JS branch at session start

  **Exposure instructions block (done):** `_renderExposureList()` now prepends a `.vc-exp-instr` panel before the word cards. Contains a mono "EXPOSURE" label, instruction copy, and a "Go to next step →" `vk-btn-outline-accent` button at the bottom-right. The old footer button at the bottom of the list is removed — the only CTA is now at the top in the instructions panel. CSS: transparent background, no border, `--pa-font-*` tokens.

  **Back button removed:** the `← Back` ghost button was removed from the `sv-header` in `#vocab-view`. Navigation back to the hub uses the sidebar.

  **Design system audit (done):** flashcard view cleaned up to use existing atelier components throughout:
  - **Round tracker** — redesigned from tab bar (bottom-border active underline) to a numbered-circle stepper. `.vocab-part-item` now renders a `.vocab-step-dot` circle (CSS-generated `1`/`2`/`3`, flips to `✓` on `.done`, accent-filled on `.active`). JS `_updateRoundDots()` unchanged — still sets `vocab-part-item active/done`.
  - **Card shell** — `vocab-card` → `pv-card`. No custom card CSS remains.
  - **French word** — `vocab-word` → `pa-utterance`. Word row uses `pv-card-top`.
  - **Icon play button** (word) — `vocab-exp-play-btn` → `pa-wi-play`. Bridge states `.playing`/`.done` added in inline CSS.
  - **Eye reveal buttons** — removed from quiz. Quiz path-a no longer has a definition-text reveal toggle; exposure uses inline reveal logic only.
  - **Transition / summary screens** — removed entirely. `_endRound()` advances directly from round 0 → round 1; session end calls `switchView('vocab-hub')`. Functions `_showTransition`, `vocabTransitionStart`, `_showFinalSummary`, `vocabRetryMissed`, `startVocabSessionFromSummary` all deleted.
  - **Font tokens** — all `--vkg-font-*` in the vocab section replaced with `--pa-font-*` (canonical atelier-scoped tokens).

  **Recall UX redesign (done):** both recall card types use a unified `.vc-row` component with consistent play/pause interaction:

  **Deck interleaving:** `_buildQuizDeck` now shuffles the Listen & Identify pool and the Read & Find pool independently, then strict-interleaves them (A, B, A, B… or B, A, B, A…, starting type random). Guarantees no two consecutive cards of the same type.

  **Unified choice row component (`.vc-row`):**
  - Both paths use the same row structure: `[vcr-num] [vcr-label?] [vcr-play]`
  - **Path A** row: number · word/expression text · play button
  - **Path B** row: number · play button (no text — definition is audio only)
  - Clicking the row (anywhere except the play button) marks it `.selected` (accent border + bg tint)
  - A **Confirm** button (`vc-path-a-submit` / `vc-path-b-submit`) is disabled until a row is selected; submitting calls `vocabPickPathA` / `vocabPickPathB`
  - After submit: rows lock (`.disabled`), correct row gets `.correct` or `.reveal`, wrong pick gets `.wrong`
  - Play button (`.vcr-play`) is a 52px tap target containing a 30px outline circle (`.vcr-circle`, accent border). Hover fills circle with `accent-bg`; playing fills solid accent + record ring. `event.stopPropagation()` so clicking play never triggers row selection.

  **Listen & Identify (Path A):**
  - Centered solid `pa-ctrl pa-ctrl-play` button (primary CTA). Choices hidden until first completed listen — revealed by `vocabDefPlayBtn`.
  - `vocabSelectA(word, rowEl)` — marks selection, enables Confirm. `vocabSubmitPathA()` → `vocabPickPathA(chosenWord)`.
  - `vocabChipPlay(word, btnEl)` — plays word TTS via `.vcr-play`, clears any other playing state.

  **Read & Find (Path B):**
  - Word shown at top; 4 definition rows (no text labels).
  - `vocabSelectB(slotIdx, rowEl)` — marks selection, enables Confirm. `vocabSubmitPathB()` → `vocabPickPathB(slotIdx)`.
  - `vocabPathBPlayBtn(slotIdx, btnEl)` — plays definition TTS for that slot; pause-if-playing on same slot.

  **Result block (both paths):** after submit, `vocab-pb-reveal` block expands below the banner showing French definition, example sentence, and hidden English translation (`vocabShowEnglish('path-a'|'path-b')`). Shared CSS — no path-specific result styles.

  **Shared CSS:** `.vc-choices`, `.vc-row`, `.vcr-num`, `.vcr-label`, `.vcr-play`, `.vcr-circle`. Path layout helpers: `.vc-path-a-play-wrap`, `.vc-path-a-divider`, `.vc-path-a-instr`, `.vc-path-b-instr`. `pa-ctrl` icon toggle via `.icon-play` / `.icon-pause` class swap.

  **Exposure round redesign (done):** all 5 cards shown at once as a scrollable list (`#vc-exposure-list`) rendered by `_renderExposureList()` — replaces the old one-card-at-a-time flow. Each card layout:
  - **Top section** — `vocab-meta-row` (POS + register badge) → word/expression + `pa-wi-play` icon button
  - **Divider** — `<hr class="vc-exp-divider">` separating word from definition
  - **Definition header** — `DÉFINITION` label (mono, uppercase) + Reveal/Hide ghost button right-aligned
  - **Definition row** — `pa-ctrl pa-ctrl-play is-outline` (blue circle, play/pause icon swap during TTS) on the left; `.vc-exp-def-content` flex column on the right (hidden until Reveal): French definition text → Translate ghost button → English text
  - **Instructions + CTA** — `.vc-exp-instr` panel prepended at the top of the list (not a footer); "Go to next step →" button inside it advances to round 1
  - No blur anywhere — definition is simply hidden until revealed
  - `_setupCard()` (used by recall rounds) hides `#vc-exposure-list` and restores `#vocab-card` display

  **Backend — `/vocab/generate` (updated):**
  - Switched from `mistral-large-latest` → `mistral-small-latest` (structured JSON task; ~4× speed improvement)
  - Temperature raised to 1.0
  - `_VOCAB_ANGLES`: 10 focus lenses (verbs, abstract nouns, idioms, formal register, etc.) randomly selected per call and injected into the system prompt as `{angle}`. Replaces the vague "be different each time" instruction which had no effect since the model has no session memory.

- ✅ **Dictation** (`#dictation-hub` + `#dictation-view`) — hub with Level (A1–C1 chips, B1 default) and Topic (7 chips + `vocab-custom-row` / `vk-input` / Add button) rows; view with loading state, audio player card (play/pause + progress bar + replay counter), textarea input, score bar + word diff + `pa-word-item` feedback list. JS was already present in the script block; backend `/dictation/generate` and `/dictation/check` already in `server.py`.

Remaining: **Coach**

For each view:
- Port the HTML
- Copy its app-specific CSS block from the current inline section
- Add its JS verbatim
- Verify before moving to the next

---

### ✅ Step 7 — Practice list + Practice Drill Tray
Ported `#practice-view` HTML and `#practice-drill-tray` + `#practice-drill-backdrop` HTML.
All CSS converted to `--vk-*` tokens. `#pl-add-modal` placed at root level outside `#app`.
`#practice-badge` span added to sidebar nav item (was a silent null-ref in index.html — now fixed).
`#practice-psdp-live-transcript-text` added to the scrollbar-hide rule alongside the other two transcript selectors.

**Layout:** instructions → tabs → content. All buttons use accent outline/text style (`vk-btn-outline-accent`). Play buttons use `pa-wi-play` with accent colour override. Word cards are compact (single row: word + play + actions). CSS fully on `--vk-*` tokens.

**Practice card width fixed:** `#practice-content` now uses `align-items: center`; cards get `width: 100%; max-width: 680px` — matching the `pv-col` width used everywhere else. Previously cards were full-width.

**Verify:** Practice list shows saved items, drill tray opens with correct content.

---

### Hub polish decisions (applied across all hubs)

- **Custom topic/subject inputs** — all hubs use `vocab-custom-row` + `vk-input` + `vk-btn vk-btn-outline` Add button. The old `hub-custom-input-wrap` + `hub-custom-add-btn` pattern (phrase hub) and bare `vk-input` without Add button (comp hub) are replaced. The `#hub-custom-topic-input` ID override that stripped `vk-input` styles is removed.
- **Hub font tokens** — `.hub-row-name`, `.hub-row-desc`, `.hub-subject-chip`, `.hub-style-divider-label` all updated from `--vkg-font-*` → `--pa-font-*`, consistent with atelier component conventions.

---

### ✅ Step 8 — Animations + motion
Added to inline `<style>` block (before `</style>`):
- `@keyframes micPulse` — pulsing box-shadow ring for mic buttons in listening state (was referenced but undefined)
- `@keyframes pa-rise` / `pa-rise-sm` — staggered entry for `.pv-ss-row` and `.pa-word-item`
- `@keyframes pa-pip-pop` — scale pop for `.phv-attempt-pip` state changes
- Stagger rules up to `:nth-child(8)` on `.pv-ss-row` (35ms steps) and `.pa-word-item` (30ms steps)
- `@starting-style` view-enter fade on all main view containers
- `@starting-style { width: 0% }` added to `.dict-score-bar-fill` (was missing; `.pv-ss-bar-fill` already had it in design system)
- `@media (prefers-reduced-motion)` disables all the above

---

### ⬜ Step 9 — Full JS audit
Confirm both script blocks are present and complete.
Remove any placeholder JS stubs added during earlier steps.
Final smoke test across all views.

---

## CSS sections to drop (not carry over)

Lines in `index.html` inline block that are **exact duplicates** of `vk-atelier-components.css` and can be deleted:

| Lines | Class(es) | Reason |
|---|---|---|
| 1085–1148 | `.pv-body`, `.pv-col`, `.pv-card`, `.pv-func`, `.pv-live-transcript`, `.pv-live-label`, `.pv-live-text` | Byte-for-byte identical to external file |
| (minor) | `.pv-func-skip` | Identical |

**Not safe to drop without verification:** `.phv-toolbar` (inline sets `margin-bottom: 0`; external sets `margin-bottom: 14px` — intentional override).

---

## Verification approach

After each step:
1. `cd /Users/josephgrgic/Documents/GitHub/Language_tool && source .venv/bin/activate && python server.py`
2. Open `http://127.0.0.1:8000/static/index-v3.html`
3. Verify the new section visually and confirm no console errors
4. Mark the step ✅ in this doc before proceeding
