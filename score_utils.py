import re
import json
import difflib
from elision import normalize_french, normalize_homophones, normalize_mute_feminine_e, strip_terminal_s

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

ANALYSIS_SYSTEM = """You are analyzing a French shadowing exercise result.

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


DICTATION_ANALYSIS_SYSTEM = """You are analyzing a French dictation exercise. The student listened to a spoken sentence and typed what they heard.

You receive:
- The target sentence (what was spoken)
- What the student typed
- A list of mismatched word pairs: {target_word, typed}

For each mismatch, provide feedback covering TWO angles where relevant:

1. PHONETIC NOTE — why might a careful listener write the wrong word?
   Explain the acoustic difference between what was said and what they wrote.
   Format: "<target_word> /<IPA>/ — <what to listen for, max 20 words>"
   Examples:
     "mangé /mɑ̃.ʒe/ — past participle ends with a held open /e/ vowel; mange ends in a near-silent schwa"
     "une /yn/ — ends with an audible /n/ that stops abruptly; un /œ̃/ is a pure nasal with no final consonant"
     "j'ai /ʒe/ — elision blends directly into the vowel; je /ʒə/ has a breathy schwa before the next word"

2. GRAMMAR NOTE — if the error reveals a grammar gap, one short sentence explaining the rule.
   If the error is purely phonetic/spelling, leave this empty string.
   Examples:
     "Passé composé: avoir + past participle — mangé is the participle, not the infinitive manger."
     "Gender agreement: beau/belle — adjective follows the noun's gender."
     "Elision: de + vowel → d' — obligatory in spoken and written French."

Classify each error as one of: tense | gender | elision | vocabulary | spelling | phonetic

Return ONLY valid JSON:
{
  "feedback": [
    {
      "target_word": "mangé",
      "typed": "mange",
      "phonetic_note": "mangé /mɑ̃.ʒe/ — past participle ends with a held /e/ vowel; mange ends in a near-silent schwa",
      "grammar_note": "Passé composé: avoir + past participle — mangé is the participle.",
      "error_type": "tense"
    }
  ]
}

If there are no mismatches, return: {"feedback": []}"""


def analyze_dictation_mismatches(target: str, typed: str, mismatches: list, client) -> list:
    """
    Call Mistral to get listening/grammar feedback for each dictation mismatch.
    client: a Mistral client instance.
    Returns list of feedback dicts.
    """
    if not mismatches:
        return []

    payload = (
        f"Target sentence: {target}\n"
        f"Student typed: {typed}\n"
        f"Mismatches: {json.dumps(mismatches)}"
    )
    fallback = [{"target_word": m["target_word"], "typed": m["typed"], "phonetic_note": "", "grammar_note": "", "error_type": "phonetic"} for m in mismatches]
    try:
        raw = client.chat.complete(
            model="mistral-small-latest",
            messages=[
                {"role": "system", "content": DICTATION_ANALYSIS_SYSTEM},
                {"role": "user", "content": payload},
            ],
            temperature=0.0,
            max_tokens=600,
        ).choices[0].message.content.strip()
        if not raw:
            return fallback
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json\n"):
                raw = raw[5:]
            raw = raw.rstrip()
        return json.loads(raw).get("feedback", [])
    except Exception:
        return fallback


def normalize(text: str, noun_adj_set=None) -> list:
    """Lowercase, normalize apostrophes, contract elisions, strip punctuation, split to word list."""
    t = text.lower()
    t = t.replace("’", "'").replace("‘", "'").replace("´", "'")
    t = normalize_french(t)
    t = _ORDINAL_RE.sub(lambda m: _ORDINAL_WORDS.get(int(m.group(1)), m.group(0)), t)
    t = t.replace("-", " ").replace("_", " ").replace("‿", " ").replace("⁀", " ")
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


def build_display_results(target: str, word_results: list, noun_adj_set=None) -> list:
    """
    Align word_results (normalized tokens) back to the original surface tokens in target.
    Hyphenated words normalize to 2 tokens, so consumes len(norm_parts) entries per display word.
    Returns list of { word, matched, said } dicts.
    """
    display_results = []
    ni = 0
    for orig_token in target.split():
        norm_parts = normalize(orig_token, noun_adj_set)
        if not norm_parts:
            continue
        wrs = word_results[ni:ni + len(norm_parts)]
        ni += len(norm_parts)
        matched = all(wr["matched"] for wr in wrs)
        said = " ".join(wr["said"] for wr in wrs if wr["said"])
        display_results.append({"word": orig_token, "matched": matched, "said": said})
    return display_results


def run_sequence_match(target_words: list, said_words: list) -> list:
    """
    Run SequenceMatcher on two normalized word lists.
    Returns word_results: list of { word, matched, said } for each target word.
    """
    word_results = [{"word": tw, "matched": False, "said": ""} for tw in target_words]
    matcher = difflib.SequenceMatcher(None, target_words, said_words, autojunk=False)
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
    return word_results


def analyze_mismatches(target: str, transcription: str, mismatches: list, client) -> list:
    """
    Call Mistral to get pronunciation tips for each mismatch.
    client: a Mistral client instance (passed in to avoid duplicating client setup).
    Returns list of feedback dicts.
    """
    if not mismatches:
        return []

    payload = (
        f"Target phrase: {target}\n"
        f"Student said: {transcription}\n"
        f"Mismatches: {json.dumps(mismatches)}"
    )
    fallback = [{"target_word": m["target_word"], "said": m["said"], "tip": "", "is_grammar": False, "grammar_note": ""} for m in mismatches]
    try:
        raw = client.chat.complete(
            model="mistral-small-latest",
            messages=[
                {"role": "system", "content": ANALYSIS_SYSTEM},
                {"role": "user", "content": payload},
            ],
            temperature=0.0,
            max_tokens=400,
        ).choices[0].message.content.strip()
        if not raw:
            return fallback
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json\n"):
                raw = raw[5:]
            raw = raw.rstrip()
        feedback = json.loads(raw).get("feedback", [])
        # Mistral normalises ‿/⁀ to spaces when echoing target_word back.
        # Re-apply the original values from the input mismatches so link marks survive.
        orig = {re.sub(r'[‿⁀]', ' ', m['target_word']): m['target_word'] for m in mismatches}
        for item in feedback:
            key = re.sub(r'[‿⁀]', ' ', item.get('target_word', ''))
            if key in orig:
                item['target_word'] = orig[key]
        return feedback
    except Exception:
        return fallback
