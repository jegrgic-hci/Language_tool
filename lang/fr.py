"""
French study-language profile.

Everything French-specific in the shared scoring pipeline (score_utils.normalize and
the Mistral feedback prompts) lives here; score_utils keeps only the
language-agnostic steps and delegates to the active profile via ``lang.get()``.

The underlying rule sets stay in their own modules (elision, liaison_rules,
pos_tagger) — this file wires them together as one profile.
"""

import re

from elision import (
    normalize_french, normalize_homophones, normalize_mute_feminine_e,
    strip_terminal_s, canonicalize_verb_endings, normalize_phonetic_homophones,
)
from liaison_rules import detect_links  # noqa: F401  (profile surface, used by engines)
from pos_tagger import tag_nouns_adjs    # noqa: F401  (profile surface, used by engines)

CODE = "fr"

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

IMPORTANT: Return exactly one feedback entry for EVERY mismatch in the list — same count, same order. Never skip, merge, or omit a mismatch, even if the transcribed word looks unrelated or nonsensical.

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


def normalize_text(t: str) -> str:
    """Text-level French normalization, applied after lowercasing/apostrophe
    unification and before tokenizing: contract elisions, spell out ordinals."""
    t = normalize_french(t)
    return _ORDINAL_RE.sub(lambda m: _ORDINAL_WORDS.get(int(m.group(1)), m.group(0)), t)


def normalize_words(words: list, phonetic: bool = False) -> list:
    """Token-level French normalization: merge sound-identical gender/number endings
    and homophones; with ``phonetic`` (speaking exercises) also canonicalize verb
    endings and speaking-only homophones."""
    words = [_EE_RE.sub("é", w) for w in words]
    words = [_EUSE_RE.sub("euse", w) for w in words]
    words = [_U_RE.sub("u", w) for w in words]
    words = normalize_homophones(words)
    words = normalize_mute_feminine_e(words)
    if phonetic:
        words = canonicalize_verb_endings(words)
        words = normalize_phonetic_homophones(words)
    return words


def strip_plural(word: str, noun_adj_set) -> str:
    """Drop the silent plural -s on tagged nouns/adjectives."""
    return strip_terminal_s(word, noun_adj_set)
