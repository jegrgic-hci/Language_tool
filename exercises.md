# Exercise Reference

All exercises in the French Tutor app. Each follows the same core loop: **generate → speak/type → score → explain**. Scoring is handled by `score_utils.py` (sequence alignment via difflib `SequenceMatcher`) unless noted otherwise.

---

## 1. Phrase Speaking

**Purpose:** Read a single AI-generated French sentence out loud and get word-level pronunciation feedback.

**Engine:** `shadow_engine.py`  
**Routes:** `POST /shadow/phrase`, `POST /shadow/analyze`, `POST /shadow/rhythm`

**Flow:**
1. Student selects level (A1–C2), topic, and style; optionally pins a sound focus or a specific word.
2. `/shadow/phrase` → Mistral (`mistral-small-latest`) generates one sentence calibrated to the level; `liaison_rules.detect_links()` annotates liaison markers if sound focus is "liaison"; `pos_tagger.tag_nouns_adjs()` tags nouns/adjectives for gender-aware scoring.
3. `edge-tts` (voice `fr-FR-DeniseNeural`) renders audio.
4. Student reads the phrase on screen, listens to the audio, then speaks into the mic (Web Speech API, fr-FR).
5. `/shadow/analyze` → `score_attempt()` aligns STT transcript against target; score = matched tokens / total tokens; pass threshold = 90%.
6. Mismatches sent to Mistral for per-word pronunciation tips.
7. `/shadow/rhythm` → `prosody_engine.annotate_phrase_rhythm()` returns syllabified/IPA/rhythm-group breakdown for display after the attempt.

**Style options:** `story` (narrative), `educational` (factual), `howto` (instructional)  
**Sound focuses:** liaison, nasal vowels, French /y/, uvular R, open/closed vowels, rhythm

**Analytics event:** `phrase_attempted`

---

## 2. Paragraph Speaking

**Purpose:** Read a multi-sentence paragraph aloud, sentence-by-sentence or in chunks. Builds sustained speaking and phrase-level flow.

**Engine:** `paragraph_engine.py`  
**Routes:** `POST /paragraph/start`, `POST /paragraph/analyze`, `POST /paragraph/analyze-patterns`

**Flow:**
1. `/paragraph/start` → Mistral (`mistral-large-latest`) generates a paragraph (3–5 sentences depending on level) on a chosen topic. Full paragraph rendered to audio by edge-tts. Sentences split by `_split_sentences()`.
2. Student reads the full paragraph on screen; can listen to the full audio first, then works sentence by sentence (or in user-selected chunk sizes 1–4).
3. `/paragraph/analyze` → `score_chunk()` scores each chunk; pass threshold scales with chunk size (75% for 1 sentence down to 50% for 4+). Returns per-sentence scores within the chunk.
4. After completing the full paragraph, `/paragraph/analyze-patterns` aggregates all mismatches — rule-based patterns (ending sounds, nasal vowels, etc.) + Mistral AI pattern analysis — into a cross-sentence summary.

**Style options:** `story`, `educational`, `howto`, `vocabulary` (dictionary definitions in French), `proverbs` (idiom + French explanation), `dialogue` (2-person conversation), `opinion` (persuasive monologue)

**Analytics events:** `paragraph_started`, `paragraph_attempted`, `paragraph_drilled` (retry mode)

---

## 3. Shadowing

**Purpose:** True blind shadowing — the phrase is hidden and audio + mic start simultaneously. Student speaks along while listening, training ear-to-mouth reaction time and natural rhythm. No text is shown until after the attempt.

**Engine:** `shadow_engine.py` (same phrase generation as Phrase Speaking)  
**Routes:** `POST /shadow/phrase`, `POST /shadow/analyze`  
**Views:** `shadow-hub` (setup), `shadow-view` (drill)

**Flow:**
1. Student picks level, topic, and style in the hub. No text is shown at setup.
2. On start, `/shadow/phrase` fetches a phrase (same generation pipeline as Phrase Speaking). Audio and mic both activate simultaneously — student speaks along as the audio plays.
3. 1.5 seconds after audio ends, STT transcript is auto-submitted to `/shadow/analyze`.
4. Score and word-match count displayed. Phrase remains hidden by default; student can tap "Reveal phrase" to see the text and a colour-coded diff of what they said.
5. Options: Next (new phrase), Try Again (same phrase replayed), or Skip.

**Key difference from Phrase Speaking:** the target text is never shown before or during the attempt. The exercise trains listening reaction and shadowing reflex, not reading-aloud accuracy.

**Analytics event:** `phrase_attempted`

---

## 4. Prosody / Sound Focus

**Purpose:** Targeted sound training — isolate one phonetic feature per session (nasal vowels, French /y/, liaison, etc.).

**Engine:** `prosody_engine.py`  
**Routes:** `GET /prosody/targets`, `POST /prosody/phrase`, `POST /prosody/analyze`

**Flow:**
1. `/prosody/targets` returns the 6 available sound targets with labels and descriptions.
2. `/prosody/phrase` → Mistral generates a phrase deliberately packed with the target sound. Response includes IPA transcription, syllabified breakdown (phonetic, word-by-word), rhythm groups, liaisons, and enchaînements.
3. Student sees the prosody display (IPA, syllables, groupes rythmiques) before and after speaking.
4. `/prosody/analyze` → same scoring pipeline as Phrase Speaking; mismatch analysis is sound-target-aware (Mistral gets the target label as context for tips).

**Sound targets:**

| Key | Label | Focus |
|---|---|---|
| `liaison` | Liaison | Mandatory linking sounds across word boundaries |
| `nasal` | Nasal Vowels | /ɑ̃/ /ɛ̃/ /ɔ̃/ — an, in, on |
| `u_vowel` | French /y/ Sound | Tight-lipped u (lune, tu, vu) |
| `r_sound` | Uvular R /ʁ/ | The French throat R |
| `open_vowels` | Open vs Closed | é /e/ vs è /ɛ/ distinction |
| `rhythm` | Rhythm & Flow | Groupes rythmiques, phrase stress |

---

## 5. Word Drill

**Purpose:** Isolated word repetition — practise a single word across multiple attempts for focused micro-drilling.

**Route:** `POST /analyze_word_drill`

**Flow:**
1. A word enters the drill from any surface: practice list, phrase drill-down, paragraph drill-down.
2. Student attempts the word N times via STT; each transcript is collected.
3. `/analyze_word_drill` calculates a hit rate (exact normalized token match). Mistral (`mistral-small-latest`) is called with either the "struggling" or "solid" system prompt (threshold: 60% hit rate).
4. Feedback is 2–4 sentences: the specific phoneme or pattern, a body-mechanics cue, and a practical tip.

**Analytics event:** `word_attempted`

---

## 6. Comprehension

**Purpose:** Listening comprehension — listen to a passage and answer multiple-choice questions (all in French).

**Route:** `POST /comprehension/generate`

**Flow:**
1. Student picks level and topic.
2. Mistral (`mistral-large-latest`) generates a JSON object containing: passage (1–4 paragraphs), `vocab_preview` (4–6 key words with French definitions and example sentences from the passage), and comprehension questions.
3. Passage rendered to audio via edge-tts.
4. Student listens (audio plays), reads questions, selects answers. Correct answer + French explanation revealed on submit. No STT in this exercise.

**Question types (in order):** literal recall → inference → vocabulary in context → main idea. Additional literal/inference questions added at higher levels.

**Question counts by level:** A1/A2 = 3, B1/B2 = 4, C1/C2 = 5

---

## 7. Dictation

**Purpose:** Listening → writing — hear a sentence and type it out exactly. Tests auditory discrimination and spelling.

**Routes:** `POST /dictation/generate`, `POST /dictation/check`, `POST /dictation/check-inline`

**Flow:**
1. `/dictation/generate` → Mistral (`mistral-large-latest`) generates one sentence calibrated to the level and topic. Sentence stored server-side by `sentence_id`. Audio rendered.
2. Student clicks to play audio (can replay), then types what they heard.
3. `/dictation/check` → `normalize()` + `run_sequence_match()` scores typed text against original sentence. `analyze_dictation_mismatches()` in `score_utils.py` asks Mistral to explain differences (spelling/hearing errors).
4. `/dictation/check-inline` is the same logic but accepts the target directly (used when sentence is already known client-side, e.g. custom content dictation).

**Sentence lengths by level:** A1 = 5–8 words, A2 = 8–12, B1 = 10–16, B2 = 12–20, C1 = 15–25, C2 = 20+

---

## 8. Vocabulary

**Purpose:** Flashcard-style vocabulary acquisition — browse a generated set of words with definitions, examples, and part-of-speech metadata.

**Route:** `POST /vocab/generate`  
**Spec:** `vocabulary.md`

**Flow:**
1. Student picks level and subject; optionally sets card count (4–20, default 8).
2. `/vocab/generate` → Mistral (`mistral-small-latest`) generates vocabulary cards with a randomly chosen angle (verbs, concrete nouns, adjectives, idioms, formal register, informal register, etc.) to vary the word selection across sessions.
3. Each card contains: word, part of speech (`verbe`/`nom`/`adjectif`/`adverbe`/`expression`/`locution`), usage register (`courant`/`familier`/`soutenu`), French definition, English definition, example sentence, English translation.
4. Cards displayed as a browsable deck. No STT or scoring — pure reading/study mode.

---

## 9. Practice List

**Purpose:** Personal word bank — save words encountered in any exercise and drill them later.

**Routes:** `GET/POST /practice-list`, `GET /practice-list/pronunciation`, `GET /practice-list/context-phrase`, `DELETE /practice-list/{word}`, `POST /analyze_word_drill`

**Storage:** JSON file under `data/`

**Flow:**
- Words/phrases/paragraphs can be added from any exercise view, or manually entered.
- On add, Mistral (`mistral-small-latest`) auto-fetches an IPA pronunciation tip + correct article (`le`/`la`/`l'`/`les`/`je` for verbs).
- `/practice-list/context-phrase` generates a natural carrier sentence (8–14 words) containing the word for contextual practice.
- Word drill: student attempts the word via STT, scoring logged across attempts, then `/analyze_word_drill` provides pattern feedback.
- Entries support `word`, `phrase`, or `paragraph` types.

---

## 10. Custom Content

**Purpose:** Bring your own French text — paste any passage to shadow or dictate.

**Routes:** `GET /custom/list`, `POST /custom/save`, `POST /custom/start`, `DELETE /custom/{entry_id}`

**Flow:**
- Student pastes text and chooses content type: `phrase` (one item per line), `paragraph` (split by sentence), or `story` (split by `----` dividers into sub-passages).
- Saved entries persist in `user_content.json`.
- `/custom/start` splits the text and generates audio; the passage then flows through the standard Paragraph Speaking interface.
- Scoring uses the same `score_chunk()` + `analyze_mismatches()` pipeline.

---

## 11. Writing Practice

**Purpose:** Short written production — respond to a French prompt in 1–2 sentences, get coaching hints (not corrections).

**Routes:** `POST /writing/prompt`, `POST /writing/check`

**Flow:**
1. `/writing/prompt` → Mistral (`mistral-small-latest`) generates a brief French task (description, opinion, short narrative, completion) calibrated to level and topic.
2. Student types their response.
3. `/writing/check` → Mistral evaluates the attempt and returns `has_errors` (bool), up to 3 pedagogical `tips`, and an `overall` encouragement sentence in English. Tips guide toward the correct form without revealing it directly.
4. Students get up to 3 attempts; hints narrow with each attempt.

**Tip style by level:**
- A1/A2: plain English rule names, maximum scaffolding, exact word named
- B1: French grammar terms introduced alongside English
- B2: French terms only, targeted nudge
- C1: one-line technical prompt

---

## 12. Sentence Transform

**Purpose:** Grammar-focused transformation drill — apply a specific grammatical operation to a source sentence.

**Routes:** `POST /transform/generate`, `POST /transform/check`

**Flow:**
1. `/transform/generate` → Mistral (`mistral-small-latest`) generates a source sentence and a one-sentence instruction in French (e.g. "Mettez cette phrase au passé composé.").
2. Student types the transformed sentence.
3. `/transform/check` → Mistral evaluates: did the transformation apply correctly? Are there agreement/conjugation errors? Is the rest of the sentence preserved? Returns same `has_errors`/`tips`/`overall` shape as Writing Practice.
4. Up to 3 attempts; hints narrow each time.

**Focus types:** tense, negation, pronoun substitution, register (tu↔vous), number (singular↔plural)  
**Levels:** A1/A2 (present, basic negation, agreement), B1 (passé composé/imparfait, futur simple, pronouns), B2 (conditionnel, subjonctif, gérondif), C1 (subjonctif passé, concordance des temps, nominalisation)

---

## Shared Infrastructure

### Scoring pipeline (speaking exercises)
1. Engine generates target text via Mistral.
2. Browser STT (Web Speech API, fr-FR) transcribes the attempt; `contractElisions()` in JS pre-contracts elisions before sending.
3. `score_utils.normalize()` tokenizes both sides: hyphen/underscore → space, elision rules from `elision.py`, homophone normalization, gender-ending normalization via `pos_tagger.py`.
4. `run_sequence_match()` (difflib `SequenceMatcher`) aligns tokens → per-word hit/miss.
5. `build_display_results()` maps back to original tokens for visual diff.
6. `analyze_mismatches()` asks Mistral to explain each mismatch as a pronunciation tip.

### Pass thresholds
| Exercise | Threshold |
|---|---|
| Phrase Speaking / Shadowing / Prosody / Word Drill | 90% |
| Paragraph (1 sentence) | 75% |
| Paragraph (2 sentences) | 65% |
| Paragraph (3 sentences) | 55% |
| Paragraph (4+ sentences) | 50% |

### Models used
| Task | Model |
|---|---|
| Paragraph, comprehension, dictation generation | `mistral-large-latest` |
| Phrase, prosody, word drill, vocab, writing, transform | `mistral-small-latest` |
| Pronunciation/mismatch analysis | `mistral-small-latest` |

### TTS
All audio is rendered by `edge-tts` (`fr-FR-DeniseNeural`). Text is cleaned by `clean_for_tts()` (strips emoji, markdown, bullets, em-dashes) before sending.
