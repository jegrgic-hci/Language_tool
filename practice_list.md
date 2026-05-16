# Practice List — Feature Reference

## Overview

The practice list is a persistent store of saved pronunciation material, accessible from the left sidebar. It supports three content types — words, phrases, and paragraphs — displayed in a tabbed interface.

---

## Data model

All items are stored in `data/practice_list.json` via `practice_list.py`.

| Field | Type | Notes |
|---|---|---|
| `id` | string (uuid) | Added to all entries; legacy entries get backfilled on load |
| `type` | string | `"word"` \| `"phrase"` \| `"paragraph"` — legacy entries default to `"word"` |
| `word` | string | The text content (single word, phrase, or full paragraph) |
| `tip` | string | Pronunciation tip (words only; empty for phrases/paragraphs) |
| `article` | string | French article (words only: `le`, `la`, `l'`, `les`, `je`) |
| `source_phrase` | string | Context phrase the word was saved from (words only) |
| `added_at` | ISO string | Creation timestamp |

---

## Backend endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/practice-list` | Returns all items |
| `POST` | `/practice-list` | Add an item — accepts `word`, `tip`, `source_phrase`, `article`, `entry_type` |
| `DELETE` | `/practice-list/{word}` | Delete a word entry by word text (legacy; words only) |
| `DELETE` | `/practice-list/id/{entry_id}` | Delete any entry by UUID (used for phrases and paragraphs) |
| `GET` | `/practice-list/pronunciation?word=` | Fetch Mistral-generated phonetic tip and article for a word |
| `GET` | `/practice-list/context-phrase?word=` | Fetch a Mistral-generated example sentence + English translation for a word |

`entry_type` on POST accepts `"word"` (default), `"phrase"`, or `"paragraph"`. Invalid values fall back to `"word"`.

---

## Saving items

### From the paragraph exercise card (`para-card-playback`)
- **Bookmark icon** — saves the full paragraph (`paragraphSentences.join(' ')`) as type `paragraph`
- **+ word button** — opens a modal to manually type a word/phrase; saved as type `word`

### From the phrase exercise card (`phrase-card-toolbar`)
- **Bookmark icon** — saves `currentPhrase` as type `phrase`

### From sentence scores (paragraph results)
- **Save button** on each `pss-row` — saves that sentence as type `phrase`

All save actions call `saveToLibrary(text, contentType, btn)` which POSTs to `/practice-list` and briefly flashes ✓ on the button.

---

## Frontend — tabbed interface

The practice list view (`#practice-view`) has three tabs rendered by `renderPracticeTab(items)`:

| Tab | Filters | FAB visible |
|---|---|---|
| Words | `type === "word"` | Yes |
| Phrases | `type === "phrase"` | Yes |
| Paragraphs | `type === "paragraph"` | No |

Tab counts are not shown. Active tab state is held in `activePracticeTab` (default `'words'`). Switching tabs calls `renderPracticeTab(_cachedPracticeItems)` without re-fetching.

### FAB (floating action button)
A `+` button (`#pl-add-fab`, `position:absolute` in `#practice-view`) opens `#pl-add-modal` — a root-level modal with a text input and a **Save as** toggle (`Word | Phrase`). The toggle auto-detects type on keystroke (1 word → Word, 2+ words → Phrase) and shows "auto-detected"; clicking either button manually locks the choice and shows "manual". Submits via Enter or the Add button, flashes ✓ for 800ms then closes.

### "How it works" footer
An `#practice-guide` overlay (`position:absolute; bottom:0`) is visible only on the Words tab. Dismiss button hides it permanently for the session (`data-dismissed='true'` prevents tab-switching from restoring it).

---

## Card behaviour by type

### Words
- Displays article + word and pronunciation tip (fetched from `/practice-list/pronunciation`)
- **Play button**: TTS of article + word
- **Practice button**: opens `#practice-drill-tray` via `enterWordDrill(word, tip, word, article)`
- Remove: DELETE by word text

### Phrases
- Displays full phrase text
- **Play button**: TTS listen-only
- **Practice button**: opens `#practice-drill-tray` via `enterWordDrill(text, '', 'Phrase Drill')`
- Remove: DELETE by UUID

### Paragraphs
- Displays first 3 lines of text; "Show all" toggle to expand
- **Play button**: TTS listen-only
- **Practice button**: calls `practiceCustomContent(text, 'paragraphs')` — launches full paragraph shadowing exercise
- Remove: DELETE by UUID

---

## Practice drill tray (`#practice-drill-tray`)

`enterWordDrill(word, tip, title, article, isWordEntry)` opens the tray. The 5th argument `isWordEntry` controls which layout is shown.

---

### Word mode (`isWordEntry = true`)

Two sections separated by a divider:

**Pronunciation section**
- Word displayed with article (e.g. `le boulanger`)
- Pronunciation tip in teal below the word
- **Play button** (`#practice-para-drill-replay-btn`): TTS of article + word
- **Active Listening button** (`#practice-psdp-word-check-btn`, 44px tall): triggers `startWordCheck` — 30-second continuous STT using a separate `wordCheckRecognition` instance. Scores each attempt against the word only; shows live interim text and a hit/miss log inline in the tray.

**In context section** (loads async via `GET /practice-list/context-phrase?word=`, mistral-small-latest)
- Appears once the phrase arrives, with a top-border divider
- Context phrase (8–14 word natural French sentence) + English translation
- **Play button** (`#practice-psdp-context-play-btn`): TTS of the phrase; audio cached after first fetch
- **Mic button** (`#practice-psdp-context-mic-btn`): single-attempt STT scoring against the context phrase. Sets `isPhraseContextDrill = true` before calling `togglePracticeDrillMic()`; `handleWordDrillResult` uses `wordDrillContextPhrase` as the target. Score, diff, and feedback items appear below.

---

### Phrase mode (`isWordEntry = false` / omitted)

Single section — no Active Listening, no In Context block:
- Phrase text displayed
- **Play button** (`#practice-para-drill-replay-btn`): TTS of the phrase
- **Mic button** (`#practice-para-drill-mic-btn`): single-attempt STT scoring against the phrase (`wordDrillWord`). No `isPhraseContextDrill` flag needed.

---

### State variables (drill tray)
| Variable | Purpose |
|---|---|
| `wordDrillWord` | The word or phrase being drilled (scoring target for Active Listening and phrase-mode mic) |
| `wordDrillContextPhrase` | Mistral-generated context sentence (scoring target for context mic in word mode) |
| `isPhraseContextDrill` | `true` while the context mic result is in flight; determines which mic button gets the listening class and which target `handleWordDrillResult` uses; reset at end of `renderWordDrillFeedback` |
| `wordDrillAudioUrl` | Cached TTS URL for the word/phrase |

`togglePracticeDrillMic()` resolves the active mic button by checking `isPhraseContextDrill`: context mic (`#practice-psdp-context-mic-btn`) when true, phrase mic (`#practice-para-drill-mic-btn`) when false.

`exitPracticeDrill()` stops both mics, calls `stopWordCheck()` if Active Listening is running, and resets all flags.

---

## Key functions

| Function | Location | Purpose |
|---|---|---|
| `loadPracticeList()` | `index.html` | Fetch all items, update badge count, render active tab |
| `renderPracticeTab(items)` | `index.html` | Render word/phrase/paragraph cards for active tab |
| `updatePracticeCount()` | `index.html` | Update sidebar badge without re-rendering |
| `saveToLibrary(text, contentType, btn)` | `index.html` | POST to `/practice-list` with correct type; flash ✓ on button |
| `addToPracticeList(word, tip, sourcePhrase, entryType)` | `index.html` | POST any entry type to `/practice-list` |
| `enterWordDrill(word, tip, title, article)` | `index.html` | Open drill tray; wire word-check mic; fetch + wire context phrase and phrase mic |
| `exitPracticeDrill()` | `index.html` | Close tray; stop phrase mic and word-check; reset all drill state |
| `startWordCheck(word, card, micBtn, interimEl, logEl, article)` | `index.html` | 30-second continuous STT session for a single word |
| `pl.add_word(word, tip, source, article, entry_type)` | `practice_list.py` | Write entry to JSON; deduplicates words in-place |
| `pl.remove_entry(entry_id)` | `practice_list.py` | Delete by UUID — used for phrases and paragraphs |
| `pl.remove_word(word)` | `practice_list.py` | Delete by word text — used for word entries |

---

## Add word modal (para-card)

A small overlay (`#para-add-word-modal`) triggered by the `+ word` button in `#para-card-playback`. Input clears on submit and shows `✓ Added` placeholder for 900ms before closing. Calls `addToPracticeList` then `loadPracticeList`.
