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
<html data-mode="light">            <!-- light border/elevation tokens -->
<body data-theme="atelier">         <!-- warm paper + blue accent palette -->
```

Stylesheets loaded in order:
1. `vk-tokens.css` — tier-1/2 tokens, dark defaults
2. `vk-components.css` — `.vk-btn`, `.vk-card`, reset
3. `vk-theme-light.css` — `[data-mode="light"]` border + fg overrides
4. `vk-theme-atelier.css` — `[data-theme="atelier"]` palette + `--pa-*` tokens
5. `vk-atelier-components.css` — `.pa-run`, `.pa-score`, `.pa-transcript-*`, `.pa-word-item`, etc. (used in the hero demo card)

All landing-page CSS uses `--vk-*` tokens only. No hardcoded hex values.

---

## Page Sections

### 1. Navbar (`lp-nav`)
- Sticky, 60px tall, `--vk-border` bottom border
- Logo wordmark (left) + "Sign in" outline button (right)
- On scroll: `background` shifts to `rgba(250, 247, 242, 0.88)` with `backdrop-filter: blur(10px)` via `.scrolled` class added by JS

### 2. Hero (`lp-hero`)
Two-column grid at ≥1024px: text left, demo card right.

**Text column:**
- Eyebrow: "French pronunciation training" (mono, uppercase)
- Headline: "Sound like you live there." (`--vk-text-display`, 800 weight)
- Subhead: leads with the core differentiator — per-word scoring against real French phonetic rules, with plain-English explanation of each miss. Ends with "Not just a score. A diagnosis."
- CTAs: "Get started" (primary) + "Sign in" (outline) — both → `login.html`
- Teacher jump link: small mono text link below the CTAs — "Are you a tutor or teacher? ↓" — anchors to `#for-teachers`

**Demo card** (visible on all breakpoints, stacks below text on mobile): simulates an actual phrase exercise result using real Atelier component classes. See [Hero Demo](#hero-demo) below.

### 3. Features (`lp-section`)
Three cards in a 3-column grid:

| Card | Tags |
|---|---|
| Speaking | Liaisons, Elisions, Rhythm, Homophones |
| Listening | Comprehension, Dictation, Native-paced speech |
| Vocabulary | Oral sessions, 4-round format, Active recall |

Each card has a 44×44 icon, `lp-feature-name` heading, `lp-feature-desc` body, and `lp-tag` pills. Cards reveal on scroll via `IntersectionObserver` (`.reveal` → `.visible`, staggered 80ms).

Note: the third card is **Vocabulary only** — there is no grammar feature. It describes the 4-round oral session format.

### 4. How It Works (`lp-section`)
3-step horizontal strip separated by vertical `--vk-border` dividers:
1. **Listen** — tool plays the phrase with a native voice
2. **Speak** — microphone captures the attempt
3. **Improve** — per-word feedback with Mistral tip for each miss

Title: "A simple loop. Repeat it daily."

### 5. The Method (`lp-section`)
Three principle cards in a 3-column grid (`lp-principle-card`). Background: `--vk-bg-alt`. No icons — principle tag label + bold title + body copy.

| Tag | Title |
|---|---|
| Production effect | Speaking encodes. Reading doesn't. |
| Corrective feedback | Feedback must be immediate and specific. |
| Motor learning | Pronunciation is a motor skill. |

Intro paragraph establishes the framing: exercises are grounded in language acquisition research, not passive memorization.

### 6. Why VraiFrench (`lp-section`)
3-column strip using `lp-how-grid` / `lp-how-step`. No step numbers. Three differentiators:
1. **A diagnosis, not a grade** — explains each miss vs. red/green score
2. **Built for the hard parts of French** — tuned for liaisons, elisions, nasals specifically
3. **Works for students and teachers** — access codes, analytics dashboard, progress tracking

### 7. For Tutors & Teachers (`lp-teacher-section`, id: `for-teachers`)
Two-column section (`lp-teacher-inner`) on a `--vk-bg-alt` background. Left: value props. Right: mock teacher dashboard card.

**Framing:** The teacher is irreplaceable. The tool handles repetition so the teacher can focus on nuance — the liaisons that feel natural, the intonation that carries meaning — which require a native speaker's ear. VraiFrench is explicitly positioned as a complement to the teacher, not a replacement.

**Value props (`lp-teacher-props`):**
1. **Reserve your time for what only you can do** — cultural nuance, register, lived French; let the tool handle repetition
2. **Know where your student has hit a ceiling** — identify sounds that have stalled and won't improve without direct coaching
3. **Make your impact visible** — objective score trends give students proof of progress, making the teacher's contribution undeniable

**Mock teacher card (`lp-tc-card`):**
- Header: "Student overview · 3 students · this week"
- Column headers: Student / Avg score / Trend / Sessions
- 3 student rows with color-coded scores: green ≥70% (`#1F5A40`), amber 40–70% (`#8A5A00`), red <40% (`--vk-error`)
- Trend column: up (green) / flat (muted) / down (red)
- "Next lesson focus" block (`lp-tc-focus`) — accent-tinted, surfaces a specific recommendation

**CTA:** "Get teacher access" → `login.html`

### 8. Bottom CTA (`lp-cta-section`)
Centred, `--vk-bg-alt` background.
- Headline: "Start closing the gap."
- Subhead: "Pick up where textbooks leave off — the real sounds of spoken French, with feedback on every word."
- Buttons: "Create an account" (primary) + "Sign in" (outline) — both → `login.html`

### 9. Footer (`lp-footer`)
One line: wordmark copy (left) + "Sign in →" link (right). Stacks on mobile.

---

## Hero Demo

The demo card accurately simulates what a real phrase exercise result looks like in the app.

**Mock data:**
- Target phrase: `les enfants jouent dehors`
- Scoring: `les en·fants` matched (pass), `jouait` said instead of `jouent` (fail — /ʒwɛ/ heard instead of /ʒu/), `dehors` not heard (unheard)
- Score: 67% · 2 of 3 matched
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
| `.pa-bar` / `.pa-bar-fill` | Score bar + fill |
| `.pa-bar-thresh.at-70` / `.at-90` | Threshold tick marks |
| `.pa-word-list` | "Words to work on" container |
| `.pa-word-item` / `.pa-wi-head` / `.pa-wi-tip` | Per-word feedback row |

**Animation sequence** (JS on `load`):

1. `+500ms` — `.pa-transcript` fades in; heard row builds token by token (stagger 200ms each)
2. `+1380ms` — `.pa-score` fades in, bar fills to 67%
3. `+1940ms` — `.pa-word-list` fades in

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
