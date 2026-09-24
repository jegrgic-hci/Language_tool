import os
import re
import json
import time
from collections import deque
from mistralai import Mistral
from dotenv import load_dotenv
from score_utils import normalize as _normalize, build_display_results, run_sequence_match, analyze_mismatches as _analyze_mismatches
from pos_tagger import tag_nouns_adjs
from liaison_rules import detect_links

load_dotenv()

_client = Mistral(api_key=os.environ.get("MISTRAL_API_KEY", "unset"))

# Rolling window of recently generated phrases per (difficulty, topic) to avoid repeats.
_recent_phrases: dict = {}
_RECENT_MAX = 20

def set_api_key(key: str):
    global _client
    _client = Mistral(api_key=key)

_PHRASE_SYSTEM = """You are generating French sentences for a shadowing exercise.

Rules:
- Generate ONE natural spoken French sentence appropriate for the given CEFR level.
- A1: 3–4 words, present tense only, high-frequency vocabulary (bonjour, merci, je suis, c'est).
- A2: 4–6 words, simple present/past, common contractions (j'ai, c'est, il y a, on va).
- B1: 6–9 words, mix of tenses, everyday idioms, liaison-heavy phrases, natural rhythm.
- B2: 9–12 words, complex clauses, subjunctive or conditional, richer vocabulary.
- C1: 12–15 words, sophisticated sentence structure, idiomatic expressions, southern/Marseille flavor welcome.
- C2: 15+ words, literary or highly idiomatic French, complex embedded clauses, register variation.
- The sentence must sound like natural spoken French.
- STYLE "story": a narrative fragment — a character, a moment, an action, an opinion on the subject. Conversational and personal.
- STYLE "educational": an informative statement that teaches a real fact about the subject (history, science, geography, culture, how things work). The learner should walk away knowing something true about the subject.
- STYLE "howto": a practical instruction or step in a process. Use imperative, infinitive, or instructional phrasing. The learner should walk away knowing how to do or use something related to the subject.

Return ONLY valid JSON in this exact shape (no markdown, no extra text):
{"phrase": "..."}"""



def score_attempt(target: str, transcription: str, noun_adj_set=None, lang: str = "fr") -> dict:
    """
    Compare transcription to target using sequence alignment (SequenceMatcher).
    Returns word_results (normalized, used for scoring/mismatches) and
    display_results (aligned to original phrase tokens, used for visual diff).
    """
    target_words = _normalize(target, noun_adj_set, phonetic=True, lang=lang)
    said_words = _normalize(transcription, noun_adj_set, phonetic=True, lang=lang)

    if not target_words:
        return {"score": 1.0, "passed": True, "mismatches": [], "word_results": [], "display_results": []}

    word_results = run_sequence_match(target_words, said_words)
    matches = sum(1 for wr in word_results if wr["matched"])
    score = matches / len(target_words)
    display_results = build_display_results(target, word_results, noun_adj_set, lang=lang)
    mismatches = [
        {"target_word": dr["word"], "said": dr["said"]}
        for dr in display_results if not dr["matched"]
    ]

    return {
        "score": round(score, 3),
        "passed": score >= 0.90,
        "mismatches": mismatches,
        "word_results": word_results,
        "display_results": display_results,
    }


_VALID_LEVELS = {"A1", "A2", "B1", "B2", "C1", "C2"}

_VALID_STYLES = {"story", "educational", "howto"}

def generate_phrase(level: str = 'A1', topic: str = None, style: str = 'story', sound_focus: str = None, focus_word: str = None) -> dict:
    """Returns {"phrase": str, "noun_adj_tokens": list[str]}."""
    if level not in _VALID_LEVELS:
        level = 'A1'
    if style not in _VALID_STYLES:
        style = 'story'
    topic_clause = f" about {topic}" if topic else ""
    if style == 'educational':
        style_clause = " Style: EDUCATIONAL — teach the learner one concrete, true fact about the subject (history, science, geography, culture, how things work). The sentence must convey real information, not just a personal anecdote."
    elif style == 'howto':
        style_clause = " Style: HOW-TO — a practical instruction or step in a process related to the subject (e.g. 'Pour prendre le métro, il faut d'abord acheter un ticket.'). Use imperative, infinitive, or instructional phrasing. The learner should walk away knowing how to do or use something."
    else:
        style_clause = " Style: STORY — a short narrative or conversational fragment about the subject, as a native speaker would mention it in real life."

    _SOUND_FOCUS_DESCRIPTIONS = {
        "liaison":     "many mandatory liaisons and enchaînements — include determiners before vowel nouns (les enfants), pronouns before vowel verbs (ils ont), prepositions before vowel words (dans un parc), pre-nominal adjectives before vowel nouns (bon ami). Do NOT add any special characters — liaison marks will be inserted automatically.",
        "nasal":       "multiple nasal vowels /ɑ̃/ /ɛ̃/ /ɔ̃/ (an/en, in/ein, on)",
        "u_vowel":     "multiple words with the French /y/ vowel (tu, lune, sur, pur, du, une)",
        "r_sound":     "multiple words with the uvular /ʁ/ sound (regarder, vraiment, partir, parler)",
        "open_vowels": "contrast between closed /e/ and open /ɛ/ (é vs è/ai/ê: j'ai été, après, fête)",
        "rhythm":      "3–4 clear rhythm groups of 2–4 words each with natural enchaînement",
    }
    sound_clause = f" Sound focus: the phrase must feature {_SOUND_FOCUS_DESCRIPTIONS[sound_focus]}." if sound_focus in _SOUND_FOCUS_DESCRIPTIONS else ""
    focus_clause = f' The phrase MUST naturally include the word "{focus_word}" — use it the way a native speaker would in conversation.' if focus_word else ""

    key = (level, topic, style, sound_focus, focus_word)
    recent = _recent_phrases.get(key, deque())
    avoid_clause = ""
    if recent:
        listed = "; ".join(f'"{p}"' for p in recent)
        avoid_clause = f" Do NOT generate any of these recently used phrases: {listed}."

    for attempt in range(3):
        try:
            resp = _client.chat.complete(
                model="mistral-small-latest",
                messages=[
                    {"role": "system", "content": _PHRASE_SYSTEM},
                    {"role": "user", "content": f"Generate a {level}-level French shadowing phrase{topic_clause}.{style_clause}{sound_clause}{focus_clause}{avoid_clause}"},
                ],
                temperature=0.9,
                max_tokens=120,
            )
            raw = resp.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json\n"):
                    raw = raw[5:]
                raw = raw.rstrip()
            data = json.loads(raw)
            phrase = data["phrase"]
            if sound_focus == "liaison":
                phrase = detect_links(phrase)

            if key not in _recent_phrases:
                _recent_phrases[key] = deque(maxlen=_RECENT_MAX)
            _recent_phrases[key].append(phrase)

            return {"phrase": phrase, "noun_adj_tokens": tag_nouns_adjs(phrase)}
        except Exception as e:
            if attempt < 2 and "429" in str(e):
                time.sleep(2 ** attempt)
                continue
            raise


def analyze_mismatches(target: str, transcription: str, mismatches: list, lang: str = "fr") -> list:
    """Call Mistral to get pronunciation tips for each mismatch."""
    return _analyze_mismatches(target, transcription, mismatches, _client, lang=lang)


# ── English (private beta) ──────────────────────────────────────────────────────
_PHRASE_SYSTEM_EN = """You are generating English sentences for a shadowing exercise. The learners are French speakers learning English.

Rules:
- Generate ONE natural spoken English sentence appropriate for the given CEFR level.
- A1: 3–5 words, present simple only, very high-frequency vocabulary (I am, this is, I like, thank you).
- A2: 5–7 words, present and simple past, common contractions (I'm, don't, it's, there's).
- B1: 7–10 words, mix of tenses (present perfect, past continuous, going to), everyday phrasal verbs and idioms.
- B2: 10–13 words, complex clauses, conditionals, passive voice, richer vocabulary.
- C1: 13–16 words, sophisticated structure, idiomatic expressions, nuanced vocabulary.
- C2: 16+ words, highly idiomatic or formal English, complex embedded clauses, register variation.
- The sentence must sound like natural spoken English, the way a native speaker actually says it — use contractions where a native speaker would.
- Write numbers as words (three, not 3). No abbreviations.
- Use plain English spelling without accents, even for loanwords (cafe, naive, fiance, cliche — not café, naïve).
- The subject may be given in French; write about it in English.
- STYLE "story": a narrative fragment — a character, a moment, an action, an opinion on the subject. Conversational and personal.
- STYLE "educational": an informative statement that teaches a real fact about the subject (history, science, geography, culture, how things work).
- STYLE "howto": a practical instruction or step in a process. Use imperative or instructional phrasing.

Return ONLY valid JSON in this exact shape (no markdown, no extra text):
{"phrase": "..."}"""

# English sound focuses: only sounds Azure grades reliably at phoneme level, and the
# ones French speakers most often get wrong. Where possible the sentence should make
# sense with the mispronounced minimal pair too, so the error can't be "corrected"
# away by context.
SOUND_FOCUS_EN = {
    "th":           "several words with the English 'th' sounds /θ/ and /ð/ (think, three, this, mother, months, clothes, with). Prefer words where a French-accented t/s/d/z substitution makes another real word (think/sink, three/tree, thank/tank, then/den)",
    "ough":         "two or more 'ough' words that are pronounced differently (through, though, thought, tough, cough, thorough, bought, enough)",
    "h_sound":      "several words that begin with an aspirated h (house, hungry, hold, heat, happy, hair), ideally ones where dropping the h makes another word (hold/old, heat/eat, hair/air, hand/and)",
    "vowel_length": "a short/long vowel contrast (ship/sheep, live/leave, full/fool, sit/seat, fill/feel), built so that either member of the pair could make sense in the sentence",
    "ed_endings":   "several regular past-tense verbs covering the three -ed endings: /t/ (walked, stopped, watched), /d/ (played, called, cleaned) and /ɪd/ (wanted, needed, visited)",
}

_ACCENT_CLAUSES = {
    "en-US": " Use American English spelling and vocabulary (color, apartment, vacation, fall).",
    "en-GB": " Use British English spelling and vocabulary (colour, flat, holiday, autumn).",
}


def generate_phrase_en(level: str = 'A1', topic: str = None, style: str = 'story',
                       locale: str = 'en-US', sound_focus: str = None) -> dict:
    """English counterpart of generate_phrase (no liaison / tagging). ``sound_focus``
    is a SOUND_FOCUS_EN key; anything else is ignored.
    Returns {"phrase": str, "noun_adj_tokens": []}."""
    if level not in _VALID_LEVELS:
        level = 'A1'
    if style not in _VALID_STYLES:
        style = 'story'
    if locale not in _ACCENT_CLAUSES:
        raise ValueError("Unsupported English locale: {!r}".format(locale))
    topic_clause = f" about {topic}" if topic else ""
    style_clause = f" Style: {style.upper()}."
    sound_clause = (f" Sound focus: the phrase must feature {SOUND_FOCUS_EN[sound_focus]}."
                    if sound_focus in SOUND_FOCUS_EN else "")
    key = ("en", locale, level, topic, style, sound_focus)
    recent = _recent_phrases.get(key, deque())
    avoid_clause = ""
    if recent:
        listed = "; ".join(f'"{p}"' for p in recent)
        avoid_clause = f" Do NOT generate any of these recently used phrases: {listed}."

    for attempt in range(3):
        try:
            resp = _client.chat.complete(
                model="mistral-small-latest",
                messages=[
                    {"role": "system", "content": _PHRASE_SYSTEM_EN},
                    {"role": "user", "content": f"Generate a {level}-level English shadowing phrase{topic_clause}.{style_clause}{sound_clause}{_ACCENT_CLAUSES[locale]}{avoid_clause}"},
                ],
                temperature=0.9,
                max_tokens=120,
            )
            raw = resp.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json\n"):
                    raw = raw[5:]
                raw = raw.rstrip()
            phrase = json.loads(raw)["phrase"]
            if key not in _recent_phrases:
                _recent_phrases[key] = deque(maxlen=_RECENT_MAX)
            _recent_phrases[key].append(phrase)
            return {"phrase": phrase, "noun_adj_tokens": []}
        except Exception as e:
            if attempt < 2 and "429" in str(e):
                time.sleep(2 ** attempt)
                continue
            raise
