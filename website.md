# Website (Landing Page)

## Overview

`static/landing.html` is the public-facing entry point for VraiFrench. It introduces the tool to two audiences — solo students and tutors/teachers — and funnels visitors into sign-in or registration. Unauthenticated users who visit `/` are served this page. Authenticated users are redirected immediately (see Auth below).

---

## Route

`server.py` root route:
```python
@app.get("/")
async def root():
    return FileResponse(BASE_DIR / "static" / "landing.html")
```

The exercise app (`index.html`) and analytics dashboard (`analytics.html`) are still accessed at their own `/static/…` paths.

---

## Auth Redirect

An inline script at the top of `<body>` checks `localStorage` for `ft_jwt` before the page renders. If a token is present, it decodes the JWT payload to read `role` and redirects:

- `teacher` / `super_admin` → `/static/analytics.html`
- all other roles (student) → `/static/index.html`

This means returning users never see the landing page — they go straight to the tool.

---

## Design System

Follows the same setup as `index.html`:

```html
<html lang="en" data-mode="light">  <!-- light border/elevation tokens -->
<body data-theme="atelier">         <!-- Tonal (M3) palette + blue accent -->
```

Fonts: Hanken Grotesk + Geist Mono from Google Fonts (same link as `index.html`).

Stylesheets loaded in order:
1. `vk-tokens.css` — tier-1/2 tokens, dark defaults
2. `vk-components.css` — `.vk-btn`, `.vk-card`, reset
3. `vk-theme-light.css` — `[data-mode="light"]` border + fg overrides
4. `vk-theme-atelier.css` — `[data-theme="atelier"]` palette + `--pa-*` tokens
5. `vk-atelier-components.css` — exercise components used throughout the page visuals; `.pa-run`, `.pa-score`, `.pa-transcript-*`, `.pa-word-item`, etc. 
6. `vk-chart.css` — `.atl-chart` trend line in the Progress section

All landing-page CSS uses `--vk-*` / `--pa-*` tokens only. No hardcoded hex values, no inline styles.

### Tonal (M3) rules on this page
- **Tone, not borders**: page `--vk-bg` → cards `--vk-surface` → nested panels `--vk-surface-lowest`. Tinted **bands** (`.lp-section.is-band`, the teacher section) use `--vk-bg-alt` and hold `--vk-surface-lowest` cards. No section rules; the footer is the only divider (`--vk-outline-variant`).
- **Shape**: cards 24px (`--vk-card-radius`), nested panels 16px, chips 8px, buttons pills, bottom CTA card 28px.
- **Type**: sentence case Hanken for every label (section labels, eyebrows, chips, column headers); Geist Mono only for numbers (step numbers, scores, trends, prices).
- **Buttons**: M3 variants — filled (`vk-btn-primary`), tonal (`vk-btn-tonal`), outlined (`vk-btn-outline`), text (`vk-btn-text`). The tonal/text/outlined overrides are defined inline in the page's `<style>` (same as `vk-atelier-staff.css`, which the landing page does not link).
- **Performance colours** (`--pa-pass` / `--pa-almost` / `--pa-fail`) only in the demo result and the mock teacher scores/trends.

---

## Page Sections

### 1. Navbar (`lp-nav`)
- Sticky, 64px M3 top app bar, flat (no border)
- Wordmark (left) + "Sign in" text button and "Get started" filled button (right)
- On scroll: `.scrolled` (added by JS) fills it with translucent `--vk-surface` + `backdrop-filter: blur(10px)`

### 2. Hero (`lp-hero`)
Headline on top (centred), product stage below.

**Text:** eyebrow "French pronunciation training" · headline "Sound like you live there." (one line on desktop, balanced wrap) · subhead (per-word scoring + plain-English diagnosis, two lines on desktop) · one button row: "Get started" (filled) + "Sign in" (outlined) → `/login`, "For tutors & teachers ↓" (text) → `#for-teachers`.

**Height budget:** kept compact so the whole stage sits above the fold on a ~900px-tall laptop window (hero padding 48/32, stage 32px below the buttons, desktop stage `min-height: 400px`, hero word tips kept to one short sentence).

**Stage (`#lp-stage`)** — a marketing cut of the phrase view's grid (not a 1:1 copy):
1. One centred main column (`#demo-main`, max 520px) holding, as in the app, two separate blocks: the demo card (`#demo-card` — phrase + score) and the speech tile below it (`#demo-speech`, `.pv-speech`). The speech tile runs the app's three states: **Speech input** (attempt types in with a caret) → **Reading it back…** (`.is-analysing` sweep) → **What I heard** (`.is-heard`, pass / fail / unheard runs recolour left to right). Then the score lands in the card and the bar fills.
2. ~0.9s later `.is-split` is added: the side column appears — attempt tiles (`.phv-tiles.is-visible`, "Attempt 1 of 10" + "Highest accuracy 67%") and "Words to work on" as separate cards (`.lp-stage-words`).
3. Desktop (≥1024px): the main column (card + speech tile) glides from centre to the left column (FLIP in JS, 560ms `cubic-bezier(0.2, 0, 0, 1)`, same as the app's `_flipSplit`); tiles rise with the shared `pa-rise-tile` stagger (180 / 240ms), word cards follow (320 / 380 / 440ms). Below 1024px the side column stacks under the card with the same rise, no glide.
4. A "Replay" text button appears after the split; it glides the card back to centre and reruns the sequence.

**Trigger:** the sequence starts when ≥40% of the stage is on screen (IntersectionObserver), not on page load, and plays once; Replay reruns it. The stage reserves `min-height: 400px` on desktop so the split doesn't push the page down. Reduced motion: final split state, no glide or rise. Grid CSS is landing-only (`lp-stage*`), deliberately not shared with `index.html`.

### 3. Features (`lp-section`)
Three cards in a 3-column grid:

| Card | Tags |
|---|---|
| Speaking | Liaisons, Elisions, Rhythm, Homophones |
| Listening | Comprehension, Dictation, Native-paced speech |
| Vocabulary | Oral sessions, 4-round format, Active recall |

Each card (`--vk-surface`, 24px) has a 48×48 tonal icon tile, `lp-feature-name` heading, `lp-feature-desc` body, and `lp-tag` M3 assist chips (8px, outline-variant border). Cards reveal on scroll via `IntersectionObserver` (`.reveal` → `.visible`, staggered 80ms).

Note: the third card is **Vocabulary only** — there is no grammar feature. It describes the 4-round oral session format.

### 4. How It Works (band)
Title: "Listen. Speak. See what you said." Three white step cards: numbered head and a one-line caption at the top, then a live visual below built from the app's own components (all `aria-hidden`). Visuals sit at the card foot (`margin-top: auto`) so they line up across cards:
1. **Listen** — tonal play button (`.pa-ctrl.is-outline`) + animated waveform, the phrase with a liaison mark (`.liaison-mark`), speed segmented control
2. **Speak** — record FAB (`.pa-ctrl-record.listening`) + speech block (`.pv-speech`) where the attempt types in, then the "Reading it back" sweep (`.is-analysing`)
3. **See what you said** — speech block switches to the heard diff (`.is-heard`, pass / fail / unheard runs) + a circular score ring (`.pa-cp`, 67%)

JS runs the sequence (`cycle()`) while `#lp-loop` is on screen and loops every ~11s; it resets when scrolled away. Reduced motion shows the final state only.

### 5. Feedback (`#feedback`) — replaced "Why VraiFrench"
Title: "Not another red-or-green score." Three groups (`.lp-fb-group`), each a row: the visual on the left, the callout(s) it illustrates on the right (stacked on mobile). Accent number markers (`.lp-mark`) tie each callout to its place in the visual:
1. **What you said, how to pronounce it, how to say the phrase** (callouts 1 + 2) — word-feedback card (`.pa-word-list`): phrase + score badge, `jouent ← you said: jouait` with the Mistral tip [1] and grammar note, then "Say the whole phrase" with the syllable line and liaison mark [2] (`.phv-syllable-line`)
2. **Drill / practice** (callout 3) — "Practice jouent" card: Practice / 10× drill / Save chips + drill pips and attempt meta (`.pa-wi-drill`, `.pa-drill-pip`)
3. **The insight** (callout 4) — paragraph pattern insight (`.pv-insight`) on its own

### 6. Progress (`#progress`, band)
Title: "Proof that it's working." Two balanced rows — row 1: Performance, Precision, This week (similar-height tiles); row 2: Precision trend chart (2 columns) + Words mastered with its stage bars (`.lp-wstage*`; renamed from `.lp-stage*`, which is the hero stage). Mirrors the student Home metrics (only metrics the app really has): Performance (pass mark 70%), Precision (pass mark 90%), Words mastered with the Mastered / Almost there / Still learning stage bars, a Precision trend line (`.atl-chart` from `vk-chart.css`, with a dashed 90% pass line), and a "This week" tile (days-practised dots + practice time). Bars fill and the line draws in once on scroll. Axis labels are overridden to `--vk-fg-2` for AA.

### 7. The Method (`lp-section`)
Three principle cards in a 3-column grid (`lp-principle-card`, `--vk-surface`). No icons — tonal chip label + bold title + body copy.

| Tag | Title |
|---|---|
| Production effect | Speaking encodes. Reading doesn't. |
| Corrective feedback | Feedback must be immediate and specific. |
| Motor learning | Pronunciation is a motor skill. |

Intro paragraph establishes the framing: exercises are grounded in language acquisition research, not passive memorization.

### 8. For Tutors & Teachers (`lp-teacher-section`, id: `for-teachers`)
Two-column section (`lp-teacher-inner`) on a `--vk-bg-alt` band. Left: value props. Right: mock teacher dashboard card (`--vk-surface-lowest`, 24px).

**Framing:** The teacher is irreplaceable. The tool handles repetition so the teacher can focus on nuance — the liaisons that feel natural, the intonation that carries meaning — which require a native speaker's ear. VraiFrench is explicitly positioned as a complement to the teacher, not a replacement.

**Value props (`lp-teacher-props`):**
1. **Reserve your time for what only you can do** — cultural nuance, register, lived French; let the tool handle repetition
2. **Know where your student has hit a ceiling** — identify sounds that have stalled and won't improve without direct coaching
3. **Make your impact visible** — objective score trends give students proof of progress, making the teacher's contribution undeniable

**Mock teacher card (`lp-tc-card`):**
- Header: "Student overview · 3 students · this week"
- Column headers (sentence case, `--vk-fg-2`): Student / Avg score / Trend / Sessions; rows split by `--vk-outline-variant`
- 3 student rows with scores in `--pa-pass` (≥70%) / `--pa-almost` (40–70%) / `--pa-fail` (<40%)
- Trend column: 40×16 sparkline + delta, up `--pa-pass` / flat `--vk-fg-2` / down `--pa-fail` (Sessions column hides under 480px)
- "Next lesson focus" block (`lp-tc-focus`) — tonal panel (`--vk-tonal` + `--vk-on-tonal`) inset in the card

**CTA:** "Create a teacher account →" tonal button → `/login?tab=register`

### 9. Pricing (`#pricing`)
Three plan cards (`lp-plan`, `--vk-surface`, 24px); the featured Teacher plan is a `--vk-tonal` card. Plan CTAs are full-width pill buttons. Hide the whole section with `display:none` until pricing is ready.

### 10. Bottom CTA (`lp-cta-section`)
One large centred `--vk-tonal` card (`lp-cta-card`, 28px) with `--vk-on-tonal` text; the outlined Sign in button is recoloured to on-tonal inside it.
- Headline: "Start closing the gap."
- Subhead: "Pick up where textbooks leave off — the real sounds of spoken French, with feedback on every word."
- Buttons: "Create an account" (primary) + "Sign in" (outline) — both → `login.html`

### 11. Footer (`lp-footer`)
One line above an outline-variant divider: wordmark copy (left) + "Sign in →" text button (right). Stacks on mobile.

---

## Hero Demo

The demo card accurately simulates what a real phrase exercise result looks like in the app.

**Mock data:**
- Target phrase: `les enfants jouent dehors`
- Scoring: `les en·fants` matched (pass), `jouait` said instead of `jouent` (fail — /ʒwɛ/ heard instead of /ʒu/), `dehors` not heard (unheard)
- Score: 67% (number + bar only; phrases show no matched-word count)
- Feedback: "jouent ← you said: jouait" + Mistral pronunciation tip

**Component classes used** (from `vk-atelier-components.css`):

| Class | Role |
|---|---|
| `.pa-transcript` | Outer diff container |
| `.pa-transcript-row` | One target or heard row |
| `.pa-transcript-label` | "target" / "heard" label |
| `.pa-transcript-target` | Target token row |
| `.pa-transcript-heard` | Heard token row |
| `.pa-token` | One target word (neutral) |
| `.pa-run.is-pass` | Heard word — matched (green) |
| `.pa-run.is-fail` | Heard word — said wrong (clay, underlined) |
| `.pa-run.is-unheard` | Target word — not heard (muted) |
| `.pa-score.is-progress` | Score block + colors the number + bar blue |
| `.pa-score-num` | Large mono score number |
| `.pa-bar.pa-lp` / `.pa-bar-fill.pa-lp-fill` | M3 linear progress score bar (thick) + fill |
| `.pa-bar-thresh.at-70` / `.at-90` | Threshold tick marks |
| `.pa-word-list` | "Words to work on" container |
| `.pa-word-item` / `.pa-wi-head` / `.pa-wi-tip` | Per-word feedback row |

Card layout deliberately mirrors the phrase result as it looks in the app (bordered card, phrase over a divider, transcript → score → word list) rather than the page's own Tonal card styling. Only the score bar was updated, to the app's `.pa-lp` markup.

**Animation sequence** (JS on `load`):

Times are from the moment the stage comes into view (not page load). Paced deliberately slower than the app so each beat can be read:

1. `+600ms` — speech tile fades in (450ms); "les enfants jouait" types in (85ms/char)
2. `~+3.4s` — after an 800ms hold, the "Reading it back…" sweep runs (1.7s)
3. `~+5.1s` — "What I heard": runs recolour (600ms each, 260ms stagger)
4. `~+6.5s` — `.pa-score` fades in, bar fills to 67% over 900ms
5. `~+8.3s` — the stage splits: glide 560ms (app speed); tiles rise at 240 / 360ms, word cards at 520 / 660 / 800ms

The "Words to work on" list lives in the side column (`.lp-stage-words`), not inside the card; it shows `jouent` and `dehors`.

The `.pa-score-num` spring animation from `vk-atelier-components.css` is suppressed inside `.lp-demo-card` so the number appears cleanly with the fade.

---

## Responsive Breakpoints

| Breakpoint | Change |
|---|---|
| `≥1024px` | Two-column hero (text + demo card); two-column teacher section |
| `<1024px` | Single-column hero (demo card stacks below text, full width); teacher section stacks |
| `<768px` | Tighter padding; feature cards stack; how-it-works steps stack; teacher card full width; footer stacks |

---

## CSS Namespace

All landing-page classes use the `lp-` prefix to avoid collisions with the design system and app styles.

| Prefix | Used for |
|---|---|
| `lp-` | All landing-page layout, section, and typography classes |
| `lp-teacher-` / `lp-tc-` | Teacher section layout and mock card internals |
| `lp-principle-` | Method/science section cards |
| `demo-` | Demo card wrappers and overrides |
| `pa-` | Atelier exercise components (from `vk-atelier-components.css`) |
| `vk-btn` | Standard button component |
