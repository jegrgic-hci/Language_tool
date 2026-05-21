# Kronos Design System — Handoff for Claude Code

This bundle is the **Kronos v3** design system, packaged so Claude Code can use it as a reference (or as an installed Agent Skill) when building UI in your codebase.

> **What this is:** a brand + token + component spec, plus the v3 CSS files and asset library.
> **What this is not:** production framework code. The v3 CSS is a clean reference layer; recreate it in whatever stack your codebase uses (React, Vue, SwiftUI, Tailwind, etc.) following the existing patterns there.

---

## What's in the bundle

| File / Folder | Purpose |
|---|---|
| `HANDOFF.md` | This file |
| `SKILL.md` | Agent-Skill manifest — makes this loadable as a Claude Code skill |
| `README.md` | Full brand documentation (voice, color, type, motion, components) |
| `colors_and_type.css` | Drop-in CSS tokens (`--k-*`, `--s-*`, `--font-*`) + semantic type classes |
| `tokens-v3.css` | v3 token layer (latest) |
| `components-v3.css` | v3 component styles (cards, buttons, inputs, nav, tags) |
| `v3-reference.html` | Rendered specimen — open in a browser to see every component |
| `assets/` | Wordmark variants + 16:9 placeholder |
| `icons/` | SVG icon set (16px viewBox, 1.5px stroke, currentColor) |

---

## Three ways to share with Claude Code

### 1. Install as a project-level Agent Skill (recommended)

Skills live in `.claude/skills/<skill-name>/` inside your repo. Drop the whole bundle in:

```
your-repo/
└── .claude/
    └── skills/
        └── kronos-design/
            ├── SKILL.md
            ├── README.md
            ├── colors_and_type.css
            ├── tokens-v3.css
            ├── components-v3.css
            ├── v3-reference.html
            ├── assets/
            └── icons/
```

`SKILL.md` already has the right frontmatter (`name: kronos-design`, `user-invocable: true`). Claude Code will pick it up automatically; you can then invoke it explicitly or just mention the brand and it will be loaded.

### 2. Install as a user-level skill (available across all your repos)

Same layout, but under your home directory:

```
~/.claude/skills/kronos-design/
```

### 3. Just point Claude Code at it

If you don't want to install it as a skill, drop the folder somewhere in your repo (e.g. `design/kronos/`) and tell Claude Code:

> "Read `design/kronos/README.md` and `design/kronos/SKILL.md` before doing any UI work. Use the tokens in `colors_and_type.css` and the components patterns in the README."

---

## First prompt to Claude Code

Once installed, a good starter prompt:

> Load the `kronos-design` skill. Then audit `<file-or-route>` against the system and propose changes. Keep our existing framework (React/Tailwind/etc); just bring the styling in line with Kronos tokens and component rules.

Or for new work:

> Load the `kronos-design` skill. Build `<feature>` using our existing stack. Tokens come from `colors_and_type.css`, component patterns from the README. Inline SVG icons only — no Lucide/Heroicons.

---

## Non-negotiables (the things developers most often miss)

These are the rules most likely to be silently broken during implementation. Surface them up front:

1. **Zero border-radius everywhere** except circular avatars.
2. **No shadows.** Hierarchy is communicated by 1px borders in three weights.
3. **Two casing registers, never mixed in one block.** UI chrome is uppercase IBM Plex Mono with wide tracking; body is sentence-case IBM Plex Sans.
4. **No icon library.** Inline SVG only, 16px viewBox, 1.5px stroke, round caps, `stroke="currentColor"`.
5. **Teal is an accent, not a CTA fill.** Primary buttons are `#1A1A1A`; teal-dark (`#4A6363`) is the *hover*.
6. **No emoji, no exclamations, no marketing voice.** Copy reads like an engineering changelog.
7. **8px grid.** Spacing tokens `--s-1` (4px) through `--s-10` (128px). The 4px token is reserved for tag padding only.
8. **Motion is 120–150ms with `cubic-bezier(.4, 0, .2, 1)`.** No bounces, no transforms on hover, only `color` / `background` / `border-color` transition.

`README.md` covers the rest (cards, hover states, layout, imagery direction).

---

## Caveats

- **Impact** is a system font and absent on most Linux machines. CSS falls back to Arial Black → Helvetica Neue. If you need a free analogue, Anton or Oswald Heavy are the closest — flag the substitution.
- **No real product imagery** ships with this bundle. Use the placeholder SVG until art is supplied; treat real photography as neutral / cool / desaturated when it arrives.
- **One product surface** is documented: the portfolio site. If you're building a new product surface (admin tool, writing reader, etc.), extend the system in a second kit rather than mutating the portfolio one.
