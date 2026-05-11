# Paragraph Shadow UI Redesign — Current State

## Status: Implemented ✓

All HTML, CSS, and JS changes are in `static/index.html`. The design is complete and matches the reference mockups.

---

## Architecture

### State Machine
The UI is driven by `#para-content[data-state]`:
- **idle**: Score ring (empty track), paragraph text, replay + mic + "Passed ✓" controls
- **listening**: Same as idle + live transcript below controls (separated by border-top)
- **feedback-pass**: Score ring fills with %, replay + mic + "Passed ✓" shown, auto-advances after 1.5s
- **feedback-fail**: Score ring fills with %, first chunk bold/rest dimmed, replay + mic + "try again" + "skip", feedback accordion (collapsed)
- **complete**: Score ring fills with %, Next Paragraph + Retry This buttons, feedback accordion (auto-expanded)

### Layout — ALL States
A single unified layout used in every state (no state-specific grid switching):

```
┌─────────────────────────────────────────────────┐
│  #para-card (white bg, CSS grid 120px | 1fr)    │
│  ┌──────────┐  ┌─────────────────────────────┐  │
│  │ score    │  │ [eye btn]                   │  │
│  │ ring     │  │ paragraph text (chunk bold, │  │
│  │ (120px)  │  │ rest dimmed)                │  │
│  └──────────┘  └─────────────────────────────┘  │
└─────────────────────────────────────────────────┘
  #para-controls (replay + mic + label/buttons)
  #para-live-transcript (border-top, listening only)
  #para-feedback-section (fail/complete only)
```

---

## Key DOM Elements

| ID / Class | Purpose |
|---|---|
| `#para-card` | White card, CSS grid 120px left + 1fr right |
| `#para-score-wrapper` | Left column — contains SVG ring, positioned relative, 120×120px |
| `#para-score` | Percentage text inside ring; hidden via CSS in idle/listening |
| `#para-score-ring-fill` | SVG circle element — updated by `updateParaScoreRing()` |
| `#para-right-col` | Right column — blur button + full text + stage label |
| `.para-blur-row` | Wraps the eye/blur button above the text |
| `#para-full-text` | Paragraph text; chunk spans get `.para-chunk-current` / `.para-chunk-dimmed` |
| `#para-stage-label` | Small muted label below text (e.g. "Chunk 1 of 3") |
| `#para-controls` | Flex column below the card |
| `#para-controls-step12` | Replay + mic + "Passed ✓" — shown in idle/listening/feedback-pass |
| `#para-pass-label` | "Passed ✓" span inside step12 row; hidden initially, shown on pass |
| `#para-controls-fail` | Replay + mic + "try again" + "skip" — shown in feedback-fail |
| `#para-controls-complete` | Next Paragraph + Retry This — shown in complete |
| `#para-live-transcript` | Live speech text, below controls, border-top separator |
| `#para-feedback-section` | Wrapper for feedback toggle + details |
| `#para-feedback-toggle` | Full-width bordered "feedback" button (`.para-feedback-hdr`) |
| `#para-feedback-details` | Expandable content: hide link + phrase diff + feedback items |
| `.para-hide-link` | "▾ hide" button inside expanded feedback |
| `#para-phrase-diff` | Colored word-by-word diff of target vs. said |
| `#para-feedback-items` | Per-word feedback cards with tip + "Save to practice list" |

---

## Key JS Functions

| Function | What it does |
|---|---|
| `setParaState(state)` | Sets `data-state`, shows/hides control rows and feedback, manages pass label |
| `updateParaScoreRing(id, pct, passed)` | Animates SVG ring fill + sets `.pass`/`.fail` class |
| `toggleParaBlur()` | Toggles `.blurred` on `#para-right-col` (blurs `#para-full-text`) |
| `toggleParaFeedbackDetails()` | Toggles `#para-feedback-details` visibility |
| `renderParaFeedback(data, userText)` | Updates ring, builds phrase diff + feedback items, calls `setParaState` |
| `renderParagraphWithChunkHighlight()` | Rebuilds `#para-full-text` with `.para-chunk-current` / `.para-chunk-dimmed` spans |
| `showParagraphSummary()` | Updates ring + feedback items for complete state, calls `setParaState('complete')` |
| `scheduleParagraphAdvance(ms)` | Auto-advances after delay (used by feedback-pass) |

### Pass label lifecycle
1. Hidden on `fetchNextParagraph()` (new paragraph load)
2. Shown in `setParaState('feedback-pass')`
3. Persists through `setParaState('idle')` (not touched)
4. Hidden in `setParaState('feedback-fail')`

---

## CSS Notes

- `#para-score` hidden via CSS selector in idle/listening — no JS needed:
  ```css
  #para-content[data-state="idle"] #para-score,
  #para-content[data-state="listening"] #para-score { display: none; }
  ```
- Blur: `#para-right-col.blurred #para-full-text { filter: blur(9px); }`
- Score ring: `transform: rotate(-90deg)` on the SVG; fill uses `stroke-dashoffset`
- `#para-score-wrapper` is `position: relative` so `.para-score-ring-center` (absolute, inset 0) overlays correctly

---

## Design Rules (Kronos)
- No border-radius anywhere
- `#para-card`: white (`--k-surface`) background, no border
- Feedback button (`.para-feedback-hdr`): `border: 1px solid --k-border-mid`, plain body font
- Controls: left-aligned (no `justify-content: center`)
- Replay button: outlined, 44×44px. Mic button: dark filled (`--k-sidebar`), 44×44px
- "try again" / "skip" / "Passed ✓": plain body font, not uppercase monospace

---

## Files Modified
- `static/index.html` — all HTML, CSS, and JS changes (single file)

## Testing Checklist
- [ ] Idle: ring shows (empty track), eye icon visible, replay + mic buttons shown
- [ ] Listening: mic turns red, live transcript appears below controls with divider
- [ ] Feedback-pass: ring fills (teal/green), "Passed ✓" shows, auto-advances after 1.5s
- [ ] Feedback-fail: ring fills (red), first chunk bold, "try again" + "skip" visible, feedback accordion collapsed
- [ ] Feedback accordion: "feedback" button expands/collapses details; "▾ hide" collapses
- [ ] Complete: Next Paragraph + Retry This buttons, feedback auto-expanded
- [ ] Blur: eye icon toggles blur on paragraph text only
- [ ] New paragraph: "Passed ✓" cleared, ring resets to empty track
