import os
import re
import json
import time
from collections import deque
from mistralai import Mistral
from dotenv import load_dotenv
from score_utils import normalize as _normalize, build_display_results, run_sequence_match, analyze_mismatches as _analyze_mismatches
from pos_tagger import tag_nouns_adjs

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



def score_attempt(target: str, transcription: str, noun_adj_set=None) -> dict:
    """
    Compare transcription to target using sequence alignment (SequenceMatcher).
    Returns word_results (normalized, used for scoring/mismatches) and
    display_results (aligned to original phrase tokens, used for visual diff).
    """
    target_words = _normalize(target, noun_adj_set)
    said_words = _normalize(transcription, noun_adj_set)

    if not target_words:
        return {"score": 1.0, "passed": True, "mismatches": [], "word_results": [], "display_results": []}

    word_results = run_sequence_match(target_words, said_words)
    matches = sum(1 for wr in word_results if wr["matched"])
    score = matches / len(target_words)
    display_results = build_display_results(target, word_results, noun_adj_set)
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
        "liaison":     "many mandatory liaison opportunities (e.g. les‿enfants, vous‿avez, ils‿ont)",
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

            if key not in _recent_phrases:
                _recent_phrases[key] = deque(maxlen=_RECENT_MAX)
            _recent_phrases[key].append(phrase)

            return {"phrase": phrase, "noun_adj_tokens": tag_nouns_adjs(phrase)}
        except Exception as e:
            if attempt < 2 and "429" in str(e):
                time.sleep(2 ** attempt)
                continue
            raise


def analyze_mismatches(target: str, transcription: str, mismatches: list) -> list:
    """Call Mistral to get pronunciation tips for each mismatch."""
    return _analyze_mismatches(target, transcription, mismatches, _client)
