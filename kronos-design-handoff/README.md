# Kronos Design System

> **KRONOS_LAB** — the portfolio system for **Joseph Grgic**, Senior UX Designer working on human-AI interaction and systems design.

A high-contrast, lab/protocol-flavored portfolio brand. Industrial typography (Impact + IBM Plex), monochrome surfaces with a single teal accent, hard 1px edges, no rounded corners outside of avatars, no shadows. The voice borrows from research and engineering manuals — clinical, brief, and faintly mechanical.

---

## Sources

Imported from a single self-contained spec document:

- `Kronos_design_system/kronos_master_spec_v4.0.html` — full master specification (atoms → molecules → organisms → states). All tokens, components, copy patterns, and brand language in this system are derived from this file.

No Figma file or live codebase was provided. There is one product surface: the **portfolio website** (Work / About / Writings / Contact).

---

## Index

Top-level files:

| File / Folder | Purpose |
|---|---|
| `README.md` | This document — context, content rules, visual foundations, iconography |
| `colors_and_type.css` | Drop-in CSS tokens (`--k-*`, `--s-*`, `--font-*`) and semantic type classes |
| `SKILL.md` | Agent-Skill-compatible entry point for downstream tools |
| `assets/` | Logos, brand marks, generic placeholders |
| `icons/` | SVG icon library (extracted from the master spec) |
| `preview/` | Specimen cards rendered into the Design System tab |
| `ui_kits/portfolio/` | Pixel-faithful recreation of the portfolio site — components + clickthrough |

The system has **one UI kit**: `ui_kits/portfolio/` (the only product surface present in the source).

---

## Content Fundamentals

**Voice: laboratory protocol.** Copy treats the website as a piece of equipment running a versioned protocol (`PROTOCOL_V4.0`), and the visitor as an authorized operator. The tone is technical and brief without being unfriendly — closer to an engineering changelog than a marketing site.

### Casing rules

Kronos has **two casing registers** that must not be mixed within a single block.

- **UPPERCASE — for all UI chrome.** Nav items, button labels, tags, form labels, section headers, captions, footers, brand wordmark. Always mono (IBM Plex Mono), always wide letter-spacing (0.08–0.25em).
- **Sentence case — for body copy.** Paragraphs, descriptions, hero subtitles, helper text. Always sans (IBM Plex Sans). Reads naturally; no shouty caps in paragraphs.

The system is explicit about this in code: a CSS reset comment notes that "uppercase is NOT global." Body copy must read like a human wrote it. UI chrome must read like a machine labeled it.

### Person & pronoun

Third-person noun phrases and equipment-style labels dominate the chrome. Where the designer speaks, he speaks in the first person, but sparingly — most of the system avoids pronouns entirely.

Examples that are on-brand:

- `PROTOCOL_V4.0` (version label)
- `KRONOS_LAB` (wordmark, always with underscore, always uppercase)
- `Senior UX Designer` (eyebrow over the hero name)
- `Query system...` (search input placeholder)
- `Enter code...` (form placeholder)
- `Acknowledge Protocol` (checkbox label)
- `Verification required for level-4 access.` (helper text)
- `View Case Study →` (CTA)
- `Specializing in human-AI interaction and systems design. Currently building at the intersection of data and product.` (hero body — sentence case, no exclamation, no first person)
- `Invalid format — use name@domain.com` (error message — direct, mechanical)
- `© 2026 — All rights reserved` (footer)

Off-brand: marketing exclamations, emoji, "Hey, I'm Joseph!", "Let's build something amazing 🚀". Friendly retail copy belongs to a different brand.

### Emoji & special characters

- **No emoji.** Not in chrome, not in body, not in section headers.
- Section IDs are written with underscores and decimal codes: `01_Atoms`, `01.1 — Typography System`, `04 — States`. Use a real em-dash (`—`), not a hyphen.
- Mid-dots and bullets are allowed as inline metadata separators: `Caption · Component ID: 01.1 · Build 4.0.0`.

### Number & version style

Versions are decimal and prominent (`v4.0`, `Build 4.0.0`). Years are bare (`2024`, `2026`). Section codes use a two-level decimal: `01.1`, `03.4`.

---

## Visual Foundations

### Color

A **black / white / grey** core with a **single teal accent** and two semantic colors (red error, green success). The teal is dusty, slightly desaturated — not bright cyan. There are no other brand colors and no gradients.

| Role | Token | Hex |
|---|---|---|
| Primary text, heavy borders, inverse surface | `--k-fg-1` / `--k-sidebar` | `#1A1A1A` |
| Secondary text | `--k-fg-2` | `#6B6B6B` |
| Muted text, captions | `--k-fg-3` | `#A0A0A0` |
| Page background | `--k-bg` | `#F7F7F7` |
| Card / input surface | `--k-surface` | `#FFFFFF` |
| Accent (teal mid) | `--k-teal` | `#7A9393` |
| Accent (teal dark, hover) | `--k-teal-dark` | `#4A6363` |
| Semantic error | `--k-red` | `#BD3E31` |
| Semantic success | `--k-green` | `#2D6A4F` |
| Border (heavy) | `--k-border-heavy` | `#1A1A1A` |
| Border (mid) | `--k-border-mid` | `#C8C8C8` |
| Border (light) | `--k-border-light` | `#E8E8E8` |

**Usage rules.** Teal is for accents only: active nav rail, active inline link hover, the `tag-teal` "Featured" badge, the inverse-on-dark wordmark accent bar. Never as a primary fill on a CTA — primary fills are `#1A1A1A`, and the *hover* swaps to teal-dark. Never use red or green as decoration; they only carry their semantic meaning.

### Typography

Three families, each with a single job.

- **Impact** — display. Used only for the wordmark and hero moments. Set at very wide tracking (`letter-spacing: 0.12–0.2em`). Always uppercase.
- **IBM Plex Mono** — UI chrome. Headings, labels, buttons, nav, captions, tags. Always uppercase, tracking 0.08–0.25em depending on size.
- **IBM Plex Sans** — body copy. Sentence case, 1rem at line-height 1.75, max 60ch.

Type scale at a glance: `0.6rem` (section dividers, footer caption), `0.65rem` (label/caption), `0.7rem` (nav, buttons), `0.75rem` (link), `0.9rem` (H3), `1rem` (body), `1.1rem` (H2), `1.5rem` (H1), `2–3.5rem` (display).

### Spacing

Strict **8px grid**. Tokens `--s-1` through `--s-10`: 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 / 96 / 128. The page padding is `--s-9` (96px). Section breaks are `--s-9` to `--s-10` of air. The 4px token is the only sub-8 value and is reserved for tag padding and ultra-tight inline groupings.

### Borders, corners & shadows

- **Corners are zero.** No rounded cards, no rounded inputs, no rounded buttons. The only round elements are circular avatars (`border-radius: 50%`).
- **Borders do the work shadows usually do.** 1px borders in three weights (heavy/mid/light) separate every surface from its container. Cards have `border: 1px solid var(--k-border-light)` and shift to `var(--k-border-heavy)` on hover.
- **No drop shadows.** No inner shadows. No glow. Elevation is communicated by border weight, not blur.
- Section heads carry a **4px-heavy bottom border** (the `.page-header` rule) — this is the system's signature horizontal rule.

### Backgrounds & imagery

- Surfaces are flat: `#F7F7F7` page, `#FFFFFF` cards, `#1A1A1A` for the inverse rail. No textures, no gradients, no patterns.
- Imagery slots are **16:9 placeholders** with a hairline border and a small mono caption like `IMAGE CONTAINER`. The system expects sober, documentary, mostly-monochrome photography when filled — not vivid stock. Treat all imagery as **neutral / cool / desaturated**; warm or saturated photos read as off-brand.
- Video placeholders are flat black (`--k-fg-1`) with a tiny mono label at center.

### Layout rules

- Fixed left **280px sidebar**, dark (`#1A1A1A`), full-height. Main content offset by sidebar width.
- Content max-width is `900px` (`--content-max`); page padding is `96px`.
- Major sections are separated by a `4px` heavy top border and the `MAJOR_SECTION` mono label.
- Grids use `repeat(auto-fill, minmax(280px, 1fr))` with `--s-5` (24px) gaps.

### Motion

- Fast and mechanical. Every transition is **120–150ms** with `cubic-bezier(0.4, 0, 0.2, 1)` easing. No bounces, no spring physics.
- Transitions are limited to `color`, `background`, and `border-color`. No scale, no translate-on-hover, no skew.
- The only spin animation in the system is the loading-state SVG (`@keyframes spin`, 1s linear infinite).

### Hover / press / focus states

- **Buttons.** Primary swaps fill from `#1A1A1A` to `#4A6363` (teal-dark). Secondary inverts (outline → filled black). Ghost swaps text + underline to teal-mid.
- **Cards.** Border darkens from light to heavy. No lift, no scale.
- **Nav links.** Inactive → grey. Hover → black. Active → black with a 2px underline; or in the sidebar, a 3px **left accent bar in teal** plus a subtle teal-tinted background (`rgba(122,147,147,0.18)`).
- **Inputs.** Border darkens from mid to heavy on focus. Error border swaps to red. No focus ring shadow.
- **Press.** Not explicitly defined; safe default is to hold the hover state. No shrink, no transform.
- **Disabled.** `opacity: 0.35`, `cursor: not-allowed`, `pointer-events: none`. Disabled labels run at `opacity: 0.4`.

### Transparency & blur

Used **sparingly**, only on the dark sidebar/footer surfaces for text hierarchy: `rgba(255,255,255,0.65)` for inactive items, `0.45` for version labels, `0.35` for muted captions, `0.12` for dividers. **No backdrop-blur anywhere.** No translucent panels over content.

### Cards

The canonical card is a project card: 1px light border, white surface, square corners, a 16:9 image slot at top, then a `var(--s-5)` (24px) body stack: tags row → `H2` title → `0.9rem` description → ghost CTA. On hover the border darkens. There is no shadow, no padding around the image (it bleeds to the card edge), and no inner divider — the image-to-body transition is the divider.

---

## Iconography

**SVG inline, hand-drawn at 16px viewBox, 1.5px stroke, round line caps & joins.** This is the entire icon system — no icon font, no Lucide/Heroicons import, no PNGs, no emoji, no Unicode glyphs standing in for icons.

The master spec ships a small set of original icons (Arrow, Menu, Close, Search, External, Check, GitHub-style mark, Instagram-style square). They are intentionally schematic: a six-line arrow is just an `M3 8H13 M13 8L9 4 M13 8L9 12`. Strokes are `currentColor`, sized at 16px in chrome and 24px at the icon-grid scale.

**Rules for adding new icons.**

1. **Match the construction.** 16×16 viewBox, 1.5px stroke, no fill, round caps & joins, `currentColor`.
2. **Keep them schematic.** Single-weight outlines. No two-tone, no filled glyphs, no detail beyond what a 1.5px stroke can show.
3. **Inline them.** Paste the SVG into the markup, not an `<img src>`. This keeps icon color tied to text color via `stroke="currentColor"`.
4. **No icon library substitutes.** Do not pull Lucide / Heroicons / Phosphor / Font Awesome. The look is hand-drawn, not librarian.

Available icons live in `icons/` (one SVG per glyph). The original brand-mark and a sample placeholder image live in `assets/`.

### Note on logos

The "logo" is a typographic wordmark — `KRONOS_LAB` set in Impact at wide tracking, with a 5px solid black left-bar accent on light backgrounds and a 5px teal left-bar on dark backgrounds. There is no logomark, no icon-shaped logo. The wordmark + accent bar **is** the logo. See `assets/wordmark-on-light.svg` and `assets/wordmark-on-dark.svg`.

---

## Caveats

- **Impact is a system font.** Most macOS / Windows machines have it; Linux generally does not. The CSS falls back to Arial Black, then Helvetica Neue. If a closer free analogue is needed (Anton, Oswald Heavy), flag and substitute.
- **No real product imagery.** The source spec uses placeholder boxes. Treat `assets/placeholder-*.svg` as the documented stand-in until real photography is added.
- **One product surface only.** The portfolio website is the only kit. If a separate product (e.g. a writing journal, a case-study reader) needs its own surface, it would be a second kit.
