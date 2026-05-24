import os
import re
import json
import time
from mistralai import Mistral
from dotenv import load_dotenv
from score_utils import normalize as _normalize, build_display_results, run_sequence_match, analyze_mismatches as _analyze_mismatches
from pos_tagger import tag_nouns_adjs

load_dotenv()

_client = Mistral(api_key=os.environ.get("MISTRAL_API_KEY", "unset"))

def set_api_key(key: str):
    global _client
    _client = Mistral(api_key=key)

TOPICS = ["la météo", "la nourriture", "les événements", "la nature", "le marché", "les transports", "la famille", "les loisirs"]

# Level-specific generation constraints
_LEVEL_CONSTRAINTS = {
    "A1": "3 sentences, present tense only, very common vocabulary (greetings, objects, simple actions), each sentence 5-7 words",
    "A2": "3–4 sentences, present and near-future tense, everyday vocabulary, each sentence 7-10 words",
    "B1": "4 sentences, varied tenses (present, passé composé, futur proche), intermediate vocabulary, natural conversational flow",
    "B2": "4–5 sentences, all tenses, rich and varied vocabulary, complex sentence structures, natural speech patterns",
    "C1": "4–5 sentences, sophisticated language, idiomatic expressions, nuanced vocabulary, advanced structures, regional flavor welcome",
    "C2": "5 sentences, mastery-level French, literary or formal register where appropriate, complex subordinate clauses, rare vocabulary, native-speaker fluency",
}

_PARAGRAPH_SYSTEM = """You are generating French paragraphs for a shadowing exercise.

Rules:
- Generate a natural French paragraph at the specified CEFR level.
- Each sentence should be distinct, grammatically correct, and naturally spoken.
- STYLE "story": a short narrative about the subject — characters, scenes, opinions, or lived experience. Conversational tone.
- STYLE "educational": informative prose that teaches the learner real facts about the subject (history, science, geography, culture, how things work). The learner should finish the paragraph having actually learned something true about the subject. Still natural to read aloud, but expository rather than narrative.
- STYLE "howto": practical instructions explaining how to do or use something related to the subject. Use imperative, infinitive, or step-by-step phrasing (D'abord… Ensuite… Enfin…). Natural to read aloud and genuinely useful.
- STYLE "vocabulary": each sentence is a dictionary-style definition of a different word or concept related to the subject, written entirely in French (no translations). Use the pattern "Un/Une [word] est [definition]." or "Le/La [word] désigne [definition]." Cover diverse nouns, verbs, and adjectives within the subject area. Definitions should be factually correct, clear, and at a level appropriate for the CEFR band.
- STYLE "proverbs": each sentence is a French proverb, idiom, or fixed expression related to the subject, followed immediately in the same sentence by a short French explanation of its meaning (e.g. "On dit 'avoir le cafard' quand on se sent triste ou déprimé."). Expressions should be authentic and in common use. The explanation must be in French only.
- STYLE "dialogue": a short two-person conversation (6–8 turns) related to the subject, written as continuous prose with em-dash speaker turns (— …). Natural spoken register. Both speakers' lines flow as a single readable passage for shadowing aloud.
- STYLE "opinion": a persuasive monologue expressing a clear point of view on something related to the subject. Use argumentative connectors (certes, néanmoins, en revanche, c'est pourquoi, il faut reconnaître que…). The speaker takes a position, develops it with reasons, and concludes. Natural to read aloud, opinionated in tone.

Return ONLY valid JSON in this exact shape (no markdown, no extra text):
{"paragraph": "..."}"""



def _split_sentences(paragraph: str) -> list[str]:
    """Split paragraph into sentences, keeping punctuation."""
    # Split on . ! ? followed by space(s), keeping the punctuation
    sentences = re.split(r'([.!?])\s+', paragraph)
    result = []
    for i in range(0, len(sentences), 2):
        if i + 1 < len(sentences):
            sent = (sentences[i] + sentences[i+1]).strip()
        else:
            sent = sentences[i].strip()
        if sent:
            result.append(sent)
    return result


def score_chunk(target: str, transcription: str, chunk_size: int = 1, noun_adj_set=None) -> dict:
    """
    Score a transcription against a target chunk (one or more sentences).
    Uses word-level matching with a threshold that scales with chunk_size.

    chunk_size: number of sentences in the chunk (used to determine pass threshold)
    Returns dict with score (float 0-1), passed (bool), word_results, display_results.
    """
    target_words = _normalize(target, noun_adj_set)
    said_words = _normalize(transcription, noun_adj_set)

    if not target_words:
        return {"score": 1.0, "passed": True, "word_results": [], "display_results": []}

    word_results = run_sequence_match(target_words, said_words)
    matches = sum(1 for wr in word_results if wr["matched"])
    score = matches / len(target_words)

    # Scale pass threshold based on chunk size
    if chunk_size == 1:
        threshold = 0.75
    elif chunk_size == 2:
        threshold = 0.65
    elif chunk_size == 3:
        threshold = 0.55
    else:  # 4+ sentences
        threshold = 0.50

    display_results = build_display_results(target, word_results, noun_adj_set)
    mismatches = [
        {"target_word": dr["word"], "said": dr["said"]}
        for dr in display_results if not dr["matched"]
    ]

    # Compute per-sentence scores using the same normalized token denominator as the overall score
    sents = _split_sentences(target) if chunk_size > 1 else [target]
    sentence_scores = []
    si = 0
    for sent in sents:
        sent_words = _normalize(sent, noun_adj_set)
        n = len(sent_words)
        sent_matched = sum(1 for wr in word_results[si:si + n] if wr["matched"])
        sentence_scores.append(round(sent_matched / n, 3) if n > 0 else 1.0)
        si += n

    return {
        "score": round(score, 3),
        "passed": score >= threshold,
        "mismatches": mismatches,
        "word_results": word_results,
        "display_results": display_results,
        "sentence_scores": sentence_scores,
    }


def generate_paragraph(level: str, topic: str, style: str = "story") -> dict:
    """
    Generate a French paragraph at the specified CEFR level on the given topic.
    Returns dict with: paragraph (full text), sentences (list), topic, level.
    """
    level = level.upper() if level else "A1"
    if level not in _LEVEL_CONSTRAINTS:
        level = "A1"
    if style not in ("story", "educational", "howto", "vocabulary", "proverbs", "dialogue", "opinion"):
        style = "story"

    constraints = _LEVEL_CONSTRAINTS[level]
    if style == "educational":
        style_clause = "Style: EDUCATIONAL — teach the learner real facts about the subject (history, science, geography, culture, how things work). Convey concrete, true information. Build new vocabulary tied to the subject."
    elif style == "howto":
        style_clause = "Style: HOW-TO — a practical guide explaining how to do or use something related to the subject. Use imperative, infinitive, or step-by-step instructional phrasing (e.g. 'D'abord... Ensuite... Enfin...'). The learner should finish the paragraph knowing how to do something real and useful."
    elif style == "vocabulary":
        style_clause = "Style: VOCABULARY — each sentence is a French-language definition of a different word or concept related to the subject. Pattern: 'Un/Une [word] est [definition].' or 'Le/La [word] désigne [definition].' Cover a mix of nouns, verbs, and adjectives. All definitions in French only — no translations. Factually correct and calibrated to the CEFR level."
    elif style == "proverbs":
        style_clause = "Style: PROVERBS & EXPRESSIONS — each sentence presents one authentic French proverb, idiom, or fixed expression related to the subject, followed immediately by a short French explanation of its meaning in the same sentence. Format: 'On dit [expression] quand/pour [explanation].' or '[Expression] signifie [explanation].' Authentic expressions in common use. All French, no translations."
    elif style == "dialogue":
        style_clause = "Style: DIALOGUE — write a short two-person conversation (6-8 turns) related to the subject using em-dash speaker turns (— …). Natural spoken register, no speaker names or labels. Both speakers' lines form a single continuous passage suitable for reading aloud as one text."
    elif style == "opinion":
        style_clause = "Style: OPINION — a persuasive monologue expressing a clear point of view on something related to the subject. Use argumentative connectors (certes, néanmoins, en revanche, c'est pourquoi, il faut reconnaître que…). The speaker takes a position, develops it with reasons, and concludes with conviction. Natural to read aloud, opinionated in tone."
    else:
        style_clause = "Style: STORY — write a short narrative or conversational passage about the subject, as a native speaker might tell it."

    for attempt in range(3):
        try:
            resp = _client.chat.complete(
                model="mistral-large-latest",
                messages=[
                    {"role": "system", "content": _PARAGRAPH_SYSTEM},
                    {"role": "user", "content": f"Generate a CEFR {level} French paragraph about {topic}. {constraints}. {style_clause}"},
                ],
                temperature=0.9,
                max_tokens=900,
            )
            raw = resp.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json\n"):
                    raw = raw[5:]
                raw = raw.rstrip()
            data = json.loads(raw)
            paragraph = data["paragraph"]
            sentences = _split_sentences(paragraph)

            return {
                "paragraph": paragraph,
                "sentences": sentences,
                "topic": topic,
                "level": level,
                "noun_adj_tokens": tag_nouns_adjs(paragraph),
            }
        except Exception as e:
            if attempt < 2 and "429" in str(e):
                time.sleep(2 ** attempt)
                continue
            raise


def analyze_mismatches(target: str, transcription: str, mismatches: list) -> list:
    """Call Mistral to get pronunciation tips for each mismatch."""
    return _analyze_mismatches(target, transcription, mismatches, _client)


_PATTERN_ANALYSIS_SYSTEM = """You are analyzing a French learner's pronunciation patterns across multiple shadowing attempts.

You are given a list of word-level mismatches (what they said vs. what they should have said).

Identify 2–3 KEY PATTERNS that show up consistently. Look for:
- IPA-level phoneme substitutions — e.g. replacing French /y/ with /u/ (très courant pour les anglophones), /ø/ with /e/, nasal vowels /ɛ̃/ /ɑ̃/ /ɔ̃/ spoken as oral + N, /ʁ/ dropped or anglicised
- Connected speech (liaisons, elisions in spoken French)
- Accent or prosody patterns (rushing, slowing, pitch changes)

For phoneme confusion patterns, use this format:
- pattern: "Phonème /X/ → /Y/" (e.g. "Phonème /y/ → /u/")
- explanation: one sentence in French with the IPA symbols and a brief body-mechanics cue, max 20 words
- examples: word pairs showing the substitution ("lune → loon", "du → dou")

Avoid one-off errors. Only surface patterns with 2+ occurrences.

Return ONLY valid JSON in this exact shape:
{
  "patterns": [
    {
      "pattern": "Short name of pattern (e.g. 'Phonème /y/ → /u/')",
      "explanation": "1–2 sentence explanation in French, conversational tone, max 20 words",
      "examples": ["example 1", "example 2"]
    }
  ]
}

If you cannot identify clear patterns, return: {"patterns": []}"""


def _detect_rule_based_patterns(mismatches: list[dict]) -> list[dict]:
    """
    Detect structural/grammatical patterns from mismatches using rules.
    Returns list of pattern dicts: { pattern, explanation, examples, count }
    """
    if not mismatches:
        return []

    patterns = {}

    # Track elision errors (j'ai vs je, de vs des, etc.)
    elision_pairs = {
        ("j'ai", "je"): "j'ai instead of je (passé composé vs. présent)",
        ("j'", "je "): "j' elision (missing the full je)",
        ("de", "des"): "de instead of des (singular vs. plural)",
        ("d'", "de "): "d' elision",
        ("m'", "me "): "m' elision",
        ("t'", "te "): "t' elision",
        ("s'", "se "): "s' elision",
        ("l'", "le "): "l' elision (masculine)",
        ("l'", "la "): "l' elision (feminine)",
        ("n'", "ne "): "n' elision",
        ("qu'", "que "): "qu' elision",
        ("c'", "ce "): "c' elision",
    }

    tense_pairs = {
        ("ai", "ais"): "Passé composé (j'ai) vs. conditional/imparfait (j'ais/aimais)",
        ("é", "er"): "Passé composé vs. infinitive (parlé vs. parler)",
        ("ait", "ais"): "Subjunctive vs. conditional",
    }

    gender_number_pairs = {
        ("un", "une"): "Masculine un vs. feminine une",
        ("le", "la"): "Masculine le vs. feminine la",
        ("es", "est"): "2nd person (tu es) vs. 3rd person (il/elle est)",
    }

    sound_pairs = {
        ("s", "z"): "S sound pronounced as Z",
        ("r", "l"): "R sound pronounced as L",
        ("é", "e"): "Closed é vs. open e",
    }

    # Combine all pattern definitions
    all_pairs = {**elision_pairs, **tense_pairs, **gender_number_pairs, **sound_pairs}

    # ── Phoneme confusion table ─────────────────────────────────────────────────
    # Each entry: (label, target_ipa, substituted_ipa, body_cue, target_fn, said_fn)
    # target_fn / said_fn: callable(word: str) -> bool
    _PHONEME_CONFUSION_TABLE = [
        (
            "Phonème /y/ → /u/",
            "/y/", "/u/",
            "lèvres arrondies et avancées pour le 'u' français — pas comme le 'ou'",
            # target word has standalone 'u' (not part of ou/au/eau/eu/œ/gu/qu)
            lambda t: bool(re.search(r'(?<![aoegq])u(?![ieoaê])', t)),
            # said word has 'ou', 'oo', or ends in a pure /u/ spelling
            lambda s: bool(re.search(r'ou|oo', s)),
        ),
        (
            "Phonème /ø/ → /e/",
            "/ø/", "/e/",
            "bouche mi-ouverte, lèvres arrondies pour le 'eu'",
            lambda t: bool(re.search(r'eu|œu', t)),
            lambda s: bool(re.search(r'\be\b|ay|ey', s)) and not bool(re.search(r'eu|œu', s)),
        ),
        (
            "Voyelle nasale /ɛ̃/ → /in/",
            "/ɛ̃/", "/in/",
            "le son nasal — bouche ouverte, air par le nez, pas de 'n' final",
            lambda t: bool(re.search(r'in\b|ain\b|ein\b|ien\b|yn\b', t)),
            lambda s: bool(re.search(r'in\b|ine\b|een\b', s)),
        ),
        (
            "Voyelle nasale /ɑ̃/ → /an/",
            "/ɑ̃/", "/an/",
            "son nasal ouvert — mâchoire basse, air par le nez, pas de 'n' détaché",
            lambda t: bool(re.search(r'an\b|en\b|am\b|em\b|ang\b|ant\b|and\b|ent\b|enc\b', t)),
            lambda s: bool(re.search(r'an\b|ane\b|ann\b', s)),
        ),
        (
            "Voyelle nasale /ɔ̃/ → /on/",
            "/ɔ̃/", "/on/",
            "son nasal arrondi — lèvres en avant, air par le nez",
            lambda t: bool(re.search(r'on\b|om\b|ond\b|ont\b', t)),
            lambda s: bool(re.search(r'on\b|one\b|own\b', s)) and not bool(re.search(r'on\b|om\b', t) and re.search(r'^on$', s)),
        ),
    ]

    phoneme_patterns: dict[str, dict] = {}
    for label, t_ipa, s_ipa, cue, t_fn, s_fn in _PHONEME_CONFUSION_TABLE:
        for mismatch in mismatches:
            target = mismatch["target_word"].lower()
            said   = mismatch["said"].lower()
            if t_fn(target) and s_fn(said):
                if label not in phoneme_patterns:
                    phoneme_patterns[label] = {
                        "t_ipa": t_ipa, "s_ipa": s_ipa, "cue": cue, "examples": [],
                    }
                ex = f"{mismatch['target_word']} → {mismatch['said']}"
                if ex not in phoneme_patterns[label]["examples"]:
                    phoneme_patterns[label]["examples"].append(ex)

    phoneme_results = []
    for label, data in phoneme_patterns.items():
        if len(data["examples"]) >= 2:
            phoneme_results.append({
                "pattern": label,
                "explanation": f"Tu remplaces {data['t_ipa']} par {data['s_ipa']} — {data['cue']}.",
                "examples": data["examples"][:3],
                "count": len(data["examples"]),
            })
    # ────────────────────────────────────────────────────────────────────────────

    for mismatch in mismatches:
        target = mismatch["target_word"].lower()
        said = mismatch["said"].lower()

        # Check if this matches any known pattern
        for (target_pat, said_pat), pattern_name in all_pairs.items():
            if target_pat.lower() in target and said_pat.lower() in said:
                if pattern_name not in patterns:
                    patterns[pattern_name] = {"examples": []}
                if mismatch["target_word"] not in patterns[pattern_name]["examples"]:
                    patterns[pattern_name]["examples"].append(f"{mismatch['target_word']} → {mismatch['said']}")

    # Build result list with counts, filter to 2–3 times threshold
    result = []
    for pattern_name, data in patterns.items():
        if len(data["examples"]) >= 2:  # Only surface if 2+ occurrences
            result.append({
                "pattern": pattern_name,
                "explanation": f"You said this {len(data['examples'])} times",
                "examples": data["examples"][:3],
                "count": len(data["examples"]),
            })

    # Merge phoneme confusions (highest priority — surface them first)
    combined = sorted(phoneme_results, key=lambda x: x["count"], reverse=True)
    combined += sorted(result, key=lambda x: x["count"], reverse=True)
    return combined[:3]


def analyze_patterns(all_mismatches: list[dict]) -> dict:
    """
    Analyze mismatches across a paragraph (or multiple paragraphs) for patterns.
    Returns both rule-based and AI-detected patterns.

    Args:
        all_mismatches: list of { target_word, said } dicts from all chunks

    Returns:
        {
            "rule_based": [
                { "pattern": str, "explanation": str, "examples": list, "count": int },
                ...
            ],
            "ai_patterns": [
                { "pattern": str, "explanation": str, "examples": list },
                ...
            ]
        }
    """
    if not all_mismatches:
        return {"rule_based": [], "ai_patterns": []}

    # Rule-based pattern detection
    rule_based = _detect_rule_based_patterns(all_mismatches)

    # AI-based pattern detection
    ai_patterns = []
    try:
        # Format mismatches for AI analysis
        mismatch_summary = json.dumps(all_mismatches[:50])  # Limit to 50 for token count

        raw = _client.chat.complete(
            model="mistral-small-latest",
            messages=[
                {"role": "system", "content": _PATTERN_ANALYSIS_SYSTEM},
                {"role": "user", "content": f"Mismatches from learner's attempts:\n{mismatch_summary}"},
            ],
            temperature=0.3,
            max_tokens=300,
        ).choices[0].message.content.strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json\n"):
                raw = raw[5:]
            raw = raw.rstrip()

        data = json.loads(raw)
        ai_patterns = data.get("patterns", [])
    except Exception:
        pass  # If AI analysis fails, we still return rule-based patterns

    return {
        "rule_based": rule_based,
        "ai_patterns": ai_patterns,
    }
