# French Tutor — Product Design Case Study

**Role:** UX Designer, Researcher, Human Factors Specialist, and Developer
**Timeline:** 8 days, concept to deployed MVP with live users
**Type:** Solo end-to-end product build (design + engineering)

---

## Overview

Built a web-based French language learning tool for a real user — an English speaker living in Marseille trying to develop listening comprehension and spoken fluency. The product went from first line of code to a deployed, access-gated MVP serving beta testers in eight days.

This was not a prototype. It is a running application with a backend LLM pipeline, real-time speech recognition, phonetic scoring, text-to-speech audio, a document ingestion system, a mobile-responsive UI, and an analytics layer — all decisions made against genuine user needs and real technical constraints.

---

## 1. Problem Definition

### The user situation
The target user is immersed in a French-speaking city but lacks structured practice. Traditional apps (Duolingo, etc.) are gamified for motivation rather than fluency. Tutors are expensive and asynchronous. The specific gap was: **no tool that lets you practice actual spoken French in realistic conversational contexts, on demand, with immediate feedback.**

Three concrete deficits identified:
1. Passive vocabulary without active recall under pressure
2. No mechanism to practice listening without immediately seeing the text
3. Spoken output with no objective feedback — only subjective "it sounded okay"

### Human Factors framing
This is a **skill acquisition problem**, not a knowledge problem. The user already understands French grammar. The bottleneck is perceptual-motor: the phonological loop, speech production timing, and the anxiety of producing speech without external confirmation. The design needed to support **deliberate practice** — not entertainment.

---

## 2. Design Principles Established Up Front

Before building anything, three interaction principles were locked:

**1. Listening before reading.**
Text should not be the default. The tutor's responses play as audio first. Reading is opt-in. This forces the user's brain to process auditory input before falling back to text comprehension — the actual skill they are building.

**2. Feedback must be immediate, specific, and non-judgmental.**
Speech recognition is imperfect. The system had to fail gracefully — distinguishing between the user's error, the API's error, and ambiguous cases — and communicate the distinction clearly.

**3. Mode clarity.**
The tool does multiple things: free conversation, roleplay scenarios, targeted drills, and document-grounded lessons. These are cognitively different tasks. Mode switching needed to be explicit and unambiguous so the user always knows what kind of practice they are in.

---

## 3. Architecture Decisions Driven by UX

### Choosing Mistral AI over OpenAI
The LLM choice was a user experience decision. Mistral is a French-founded model with superior native French output — idiomatic phrasing, natural contractions, correct register. Testing on the target domain (Marseille street French, market conversations, administrative contexts) confirmed measurably better quality than alternatives. The backend uses two model tiers: `mistral-large` for tutoring responses and `mistral-small` for fast intent routing and coherence checks, minimising latency on the critical path.

### Intent routing as a UX primitive
Rather than asking users to explicitly select a mode via buttons, the system classifies every message with a lightweight router (`router.py`) that infers intent: is this a request for conversation, a roleplay scenario, a drill, or a document-based question? The mode indicator in the sidebar updates in real time. Users got the right experience without navigating a menu — a significant cognitive load reduction for language learners already managing two languages simultaneously.

### Coherence checking as a dignity layer
One of the trickiest problems in voice-driven apps: the speech-to-text API occasionally returns garbage, especially for learners with accented output. Rather than surfacing that garbage to the LLM (which would produce a confused, confidence-breaking response), a lightweight coherence check intercepts incoherent transcriptions. The system responds with a targeted clarification bubble — styled differently from tutor responses — inviting the user to repeat rather than silently producing a wrong answer. This is a direct Human Factors intervention: **protect the user's confidence** when the technology fails.

---

## 4. Language Processing Challenges

### The elision problem (and its resolution)
French spoken language uses elision: *je ai* becomes *j'ai*, *tu as* becomes *t'as*. Web Speech API returns transcribed speech in its expanded or phonetically-interpreted form. The tutor's scoring system was comparing user speech to reference text.

The initial approach expanded elisions into two tokens for comparison — `j'ai` → `[je, ai]`. This created token alignment failures, introduced a dropout correction hack, and produced incorrect scores. Elided words like *l'heure* were being marked wrong even when correctly pronounced.

The fix was architecturally cleaner: treat elisions as **single tokens end-to-end**. `elision.py` became the single source of truth — a canonical rule list that contracts expanded speech-API output rather than expanding reference text. The same rules were mirrored in JavaScript in the frontend (`contractElisions()`) so that the live transcript display and the backend scorer were always operating on identical token representations. This eliminated the alignment problem entirely and removed the dropout hack.

**Human Factors significance:** scoring errors in pronunciation feedback are not just annoying — they break trust in the system and cause learners to second-guess correct output. Getting this right was essential to the tool's credibility.

### Hyphenated compounds
French compounds like *sous-estimé* are one visual token in text but two spoken phonemes that the speech API returns as separate words. The scoring engine was extended to split hyphenated tokens before comparison, so SequenceMatcher has two near-miss pairs to evaluate rather than one complete miss — giving partial credit for correct phonetic proximity.

### Monosyllable recognition failure
Chrome's fr-FR speech recognition doesn't commit results for isolated monosyllables — words like *pain*, *mot*, *force* — because there is insufficient phonetic context. The practice list feature addressed this with **carrier phrases**: instead of prompting the user to say a bare word, the interface prompts them to say *le pain* or *la force*. Scoring checks only for the target word within the carrier phrase. This is a direct workaround for a known browser API limitation, surfaced from user testing and solved without changing the scoring algorithm.

---

## 5. Interface Design

### Design system: Kronos
The visual language was not improvised — it runs from a defined specification. Kronos is a high-contrast, utilitarian system: IBM Plex Mono for UI elements and data, IBM Plex Sans for readable content, Impact for identity. No border-radius anywhere. A dark sidebar (`#1A1A1A`) for controls, a light content area for conversation. The teal accent (`#7A9393`) marks interactive and active states; red (`#BD3E31`) is reserved strictly for destructive or stop actions.

This was a deliberate Human Factors choice: **colour should mean something**. Users learn teal = active/engaged, red = stop. The system is consistent enough that this mapping becomes automatic within minutes.

### Two-panel layout
Controls (mode, topic, session state, settings) live in a fixed left panel. The conversation lives in the right panel. This reflects a mental model separation: the left panel is the cockpit, the right panel is the conversation. Users can adjust mode or toggle listening mode without losing their place in the dialogue.

### Listening mode
A core interaction paradigm: one toggle blurs all tutor response bubbles. The user hears the audio, processes it auditorily, and then clicks to reveal text if needed. This is a direct implementation of the **input before scaffolding** principle from second-language acquisition research. Individual bubbles can be revealed one at a time — granular control without cognitive overhead.

### Mobile adaptation
The original layout assumed a desktop. Beta testers accessed the tool on phones. The mobile redesign was not a scaled-down desktop — it was a different layout pattern:
- Sidebar becomes a slide-out panel behind a persistent hamburger button
- The paragraph shadow scoring ring (appropriate for desktop at 120px) is hidden on mobile; a horizontal progress bar replaces it
- Score results, per-sentence breakdowns, and controls move to a sticky bottom tray — following a mobile pattern users already know from maps and media players

The feedback accordion (which showed textual mismatch analysis) was removed entirely on mobile. Per-sentence score rows with word-level highlighting replaced it — same information, less vertical space, no interaction required to reveal it.

---

## 6. Practice List — Active Listening System

The practice list started as a simple vocabulary clipboard: words flagged during shadowing exercises. It evolved into an active practice mode.

Each word gets:
- Its definite article (`le`, `la`, `l'`) fetched from the LLM and cached
- A pronunciation tip generated by the LLM
- A play button that speaks the carrier phrase via TTS

The **Active Listening** session runs 30-second speech recognition loops with auto-restart (Chrome stops recognition after ~7s of silence even on continuous mode — the system detects this and relaunches silently). All five STT alternatives are scored per attempt, not just the top result — because for short words and learner accents, the correct transcription frequently appears in alternatives 2–5. Confidence gating was explicitly removed after testing: Chrome returns confidence=0 for correct monosyllables, which would have blocked legitimate hits.

---

## 7. Analytics

To understand how beta testers were actually using the tool — and to support future iteration — a lightweight event tracking system was built on SQLite. Seven event types are captured: session start, shadowing time on task, phrase and paragraph attempts with scores, chunk listen counts, and sentence drill events.

All raw events are stored rather than just aggregates. This was a deliberate design choice: aggregate views answer current questions (how much time? what scores?), but raw events enable correlation analysis that hasn't been defined yet (does listening more before attempting correlate with higher scores? do sentence drills improve subsequent chunk scores?). A per-access-code analytics dashboard was built at `/analytics/dashboard`, styled consistently with the Kronos system.

---

## 8. Deployment and Access Architecture

The tool is deployed on Render. Access is gated by codes — each beta tester has a code that is stored on every analytics event, enabling per-user analysis without collecting personal data. The access code system also provides a path to future monetisation without architectural change.

Security decisions made consciously:
- Audio filenames validated against a strict regex before serving (path traversal prevention)
- Upload filenames sanitised through `Path().name` (directory escape prevention)
- CORS, TTS cleanup (markdown stripped before audio generation), and session isolation all implemented from the start

---

## 9. Key Outcomes After 8 Days

| What was built | Scale |
|---|---|
| Backend API | 1,029 lines, 15+ routes |
| Frontend (single file) | 6,402 lines |
| Language processing engines | 4 modules (shadow, paragraph, elision, drills) |
| Session modes | 4 (Chat, Scenario, Drill, RAG) |
| TTS + STT pipeline | Real-time, fr-FR |
| Mobile-responsive layout | Full breakpoint redesign |
| Analytics system | SQLite, 7 event types, dashboard |
| Deployment | Live on Render, access-gated |

---

## 10. What This Demonstrates

**Human Factors in practice:** Every interaction design decision — listening mode, carrier phrases, coherence bubbles, colour semantics, mobile tray layout — came from understanding where the technology fails and where the user is cognitively vulnerable. The job was not to build features. It was to remove friction from a specific human skill acquisition task.

**Full-stack ownership under constraint:** Design, language processing logic, backend, frontend, and deployment were all executed in 8 days by one person. Constraints (Python 3.9 type syntax, async TTS, Chrome STT API edge cases, Render free tier startup behaviour) were diagnosed and solved as they appeared. No constraint became a blocker.

**Iterative refinement driven by evidence:** The elision scoring rewrite, the monosyllable carrier phrase solution, the confidence-gate removal, and the mobile layout redesign were all responses to observed failures — from testing, from user behaviour, or from API edge cases. The product improved through investigation, not guesswork.

---

*Deployed MVP — French language learning tool for immersed adult learners, May 2026.*
