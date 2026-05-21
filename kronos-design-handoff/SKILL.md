---
name: kronos-design
description: Use this skill to generate well-branded interfaces and assets for KRONOS_LAB (Joseph Grgic's portfolio brand), either for production or throwaway prototypes/mocks/etc. Contains essential design guidelines, colors, type, fonts, assets, and UI kit components for prototyping.
user-invocable: true
---

Read the `README.md` file within this skill, and explore the other available files.

If creating visual artifacts (slides, mocks, throwaway prototypes, etc), copy assets out and create static HTML files for the user to view. If working on production code, you can copy assets and read the rules here to become an expert in designing with this brand.

If the user invokes this skill without any other guidance, ask them what they want to build or design, ask some questions, and act as an expert designer who outputs HTML artifacts _or_ production code, depending on the need.

## Quick reference

- **Voice:** laboratory / protocol. Equipment-style labels in chrome (uppercase mono), sentence-case body. No emoji, no exclamations.
- **Colors:** B&W core (`#1A1A1A` / `#6B6B6B` / `#A0A0A0` / `#F7F7F7` / `#FFF`), single teal accent (`#7A9393` / `#4A6363`), semantic red (`#BD3E31`) and green (`#2D6A4F`).
- **Type:** Impact (display only), IBM Plex Mono (UI chrome, uppercase), IBM Plex Sans (body, sentence case).
- **Edges:** hard. `border-radius: 0` everywhere except circular avatars.
- **No shadows.** Borders carry hierarchy (heavy `#1A1A1A` / mid `#C8C8C8` / light `#E8E8E8`).
- **Spacing:** 8px grid via `--s-1` through `--s-10`.
- **Icons:** inline SVG, 16px viewBox, 1.5px stroke, round caps. Do not pull Lucide / Heroicons.
- **Motion:** 120–150ms, `cubic-bezier(.4, 0, .2, 1)`. No bounces, no transforms on hover.

## Files

| File | What it's for |
|---|---|
| `README.md` | Full brand documentation: content fundamentals, visual foundations, iconography |
| `colors_and_type.css` | Drop-in tokens + semantic type classes (`--k-*`, `.type-*`) |
| `assets/` | Wordmark (light / dark variants), placeholder image |
| `icons/` | Individual SVG glyphs — arrow, menu, close, search, external, check, github, instagram, spinner |
| `preview/` | Specimen cards — one tokenized concept per file |
| `ui_kits/portfolio/` | Working React recreation of the portfolio site (Sidebar, Hero, ProjectGrid, ContactForm, Footer) |

## When generating new work

1. Start from `colors_and_type.css`. Don't invent colors.
2. Copy components out of `ui_kits/portfolio/` rather than rewriting them.
3. Keep chrome uppercase mono, body sentence-case sans — never mix within a block.
4. New icons match the 16/1.5/round-cap construction; don't import an icon library.
5. If you need a flourish, it's almost certainly more border weight or more whitespace — not color, not shadow, not gradient.
