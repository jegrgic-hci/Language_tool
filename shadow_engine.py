import os
import re
import json
import time
import difflib
import unicodedata
from collections import deque
from mistralai import Mistral
from dotenv import load_dotenv
from elision import normalize_french, normalize_homophones, normalize_mute_feminine_e, strip_terminal_s

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
- The sentence must be something a native French speaker would actually say in conversation.

Return ONLY valid JSON in this exact shape (no markdown, no extra text):
{"phrase": "...", "noun_adj_tokens": ["word1", "word2"]}

noun_adj_tokens must list every noun and adjective in the phrase exactly as written."""

_ANALYSIS_SYSTEM = """You are analyzing a French shadowing exercise result.

The student was asked to repeat a French phrase exactly. You are given:
- The target phrase (what they should have said)
- The transcription (what speech recognition captured)
- A list of mismatched word pairs (target_word vs transcribed_word)

For each mismatch, provide:
1. A pronunciation tip in EXACTLY this format: "<target_word> /<IPA>/ — <body-mechanics cue>"
   - Write the target word exactly as given, then its IPA transcription between slashes
   - After the em-dash: one short body-mechanics cue — lip position, tongue placement, nasal vs. oral airflow, silent letter, etc.
   - Examples:
     "escaliers /ɛs.ka.lje/ — tongue tip behind upper teeth on the 'l', final 's' silent"
     "voudrais /vu.dʁɛ/ — lips rounded for 'ou', uvular 'r' at the back of the throat"
     "lune /lyn/ — lips pursed forward in a tight circle for the French 'u'"
     "m'appelle /ma.pɛl/ — lips forward on the 'a', final 'l' is light, not silent"
   - Max 20 words total. Never deviate from this format.
2. Whether this is a grammar/tense distinction (e.g. j'ai vs je, elision vs full form)
3. If it IS a grammar distinction, a one-sentence grammar note

French elision rules to recognize:
- "j'" vs "je": elision before vowel — relevant for tense (j'ai = passé composé aux, je = present)
- "m'" vs "me", "t'" vs "te", "s'" vs "se", "l'" vs "le/la", "n'" vs "ne", "d'" vs "de", "qu'" vs "que"
- These contractions are standard written and spoken French, not optional

Return ONLY valid JSON in this exact shape:
{
  "feedback": [
    {
      "target_word": "voudrais",
      "said": "voulait",
      "tip": "voudrais /vu.dʁɛ/ — lips rounded for 'ou', uvular 'r' at the back of the throat",
      "is_grammar": false,
      "grammar_note": ""
    }
  ]
}

If there are no mismatches to analyze, return: {"feedback": []}"""


# Strip all punctuation including apostrophes — elisions like j'ai and jai score identically
_PUNCT_RE = re.compile(r"[^\w\s]")
# Collapse phonetically identical gender/number endings:
# -ée/-ées/-és → é  (past participles of -er verbs: mangé/mangée/mangées/mangés)
# -ue/-ues/-us → u  (-u class: devenu/devenue/devenus/devenues all sound like /dəvny/)
# -euses → -euse    (feminine plural of -eux adjectives: dangereuses/dangereuse both /øz/)
_EE_RE = re.compile(r"ées?\b|és\b")
_U_RE = re.compile(r"ues?\b|us\b")
_EUSE_RE = re.compile(r"euses\b")
# Ordinal notation → spoken word (1er → premier, 2ème → deuxième, etc.)
_ORDINAL_WORDS = {
    1: "premier", 2: "deuxième", 3: "troisième", 4: "quatrième",
    5: "cinquième", 6: "sixième", 7: "septième", 8: "huitième",
    9: "neuvième", 10: "dixième", 11: "onzième", 12: "douzième",
    13: "treizième", 14: "quatorzième", 15: "quinzième", 16: "seizième",
    17: "dix septième", 18: "dix huitième", 19: "dix neuvième",
    20: "vingtième", 21: "vingt et unième", 30: "trentième",
    40: "quarantième", 50: "cinquantième", 100: "centième",
}
_ORDINAL_RE = re.compile(r"\b(\d+)(?:ières?|ièmes?|èmes?|ères?|ers?|e)\b", re.IGNORECASE)


def _normalize(text: str, noun_adj_set=None) -> list[str]:
    """Lowercase, normalize apostrophes, contract elisions, strip punctuation, split to word list."""
    t = text.lower()
    t = t.replace("’", "’").replace("’", "’").replace("´", "’")
    t = normalize_french(t)
    t = _ORDINAL_RE.sub(lambda m: _ORDINAL_WORDS.get(int(m.group(1)), m.group(0)), t)
    t = t.replace("-", " ")
    t = _PUNCT_RE.sub("", t)
    words = [w for w in t.split() if w]
    words = [_EE_RE.sub("é", w) for w in words]
    words = [_EUSE_RE.sub("euse", w) for w in words]
    words = [_U_RE.sub("u", w) for w in words]
    words = normalize_homophones(words)
    words = normalize_mute_feminine_e(words)
    if noun_adj_set:
        words = [strip_terminal_s(w, noun_adj_set) for w in words]
    return words


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

    matcher = difflib.SequenceMatcher(None, target_words, said_words, autojunk=False)

    # Build per-normalized-word result list
    word_results = [{"word": tw, "matched": False, "said": ""} for tw in target_words]

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                word_results[i1 + k]["matched"] = True
                word_results[i1 + k]["said"] = said_words[j1 + k]
        elif tag == "replace":
            for k in range(i1, i2):
                sw_idx = j1 + (k - i1)
                if sw_idx < j2:
                    word_results[k]["said"] = said_words[sw_idx]

    matches = sum(1 for wr in word_results if wr["matched"])
    score = matches / len(target_words)

    # Build display_results aligned to original phrase tokens.
    # Hyphenated words normalize to 2 tokens, so consume len(norm_parts) entries per display word.
    display_results = []
    ni = 0
    for orig_token in target.split():
        norm_parts = _normalize(orig_token, noun_adj_set)
        if not norm_parts:
            continue  # punctuation-only token
        wrs = word_results[ni:ni + len(norm_parts)]
        ni += len(norm_parts)
        matched = all(wr["matched"] for wr in wrs)
        said = " ".join(wr["said"] for wr in wrs if wr["said"])
        display_results.append({"word": orig_token, "matched": matched, "said": said})

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

def generate_phrase(level: str = 'A1', topic: str = None) -> dict:
    """Returns {"phrase": str, "noun_adj_tokens": list[str]}."""
    if level not in _VALID_LEVELS:
        level = 'A1'
    topic_clause = f" about {topic}" if topic else ""

    key = (level, topic)
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
                    {"role": "user", "content": f"Generate a {level}-level French shadowing phrase{topic_clause}.{avoid_clause}"},
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

            return {"phrase": phrase, "noun_adj_tokens": data.get("noun_adj_tokens", [])}
        except Exception as e:
            if attempt < 2 and "429" in str(e):
                time.sleep(2 ** attempt)
                continue
            raise


def analyze_mismatches(target: str, transcription: str, mismatches: list[dict]) -> list[dict]:
    """
    Call Mistral to get pronunciation tips for each mismatch.
    Returns list of feedback dicts (same shape as _ANALYSIS_SYSTEM specifies).
    """
    if not mismatches:
        return []

    payload = (
        f"Target phrase: {target}\n"
        f"Student said: {transcription}\n"
        f"Mismatches: {json.dumps(mismatches)}"
    )
    try:
        raw = _client.chat.complete(
            model="mistral-small-latest",
            messages=[
                {"role": "system", "content": _ANALYSIS_SYSTEM},
                {"role": "user", "content": payload},
            ],
            temperature=0.0,
            max_tokens=400,
        ).choices[0].message.content.strip()
        if not raw:
            return [{"target_word": m["target_word"], "said": m["said"], "tip": "", "is_grammar": False, "grammar_note": ""} for m in mismatches]
        # Strip markdown code blocks if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json\n"):
                raw = raw[5:]
            raw = raw.rstrip()
        data = json.loads(raw)
        return data.get("feedback", [])
    except Exception:
        return [{"target_word": m["target_word"], "said": m["said"], "tip": "", "is_grammar": False, "grammar_note": ""} for m in mismatches]
