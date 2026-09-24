"""
English study-language profile (private beta — see ENGLISH_BETA_USERS).

Same surface as lang/fr.py. English differs from French in what makes a correct
answer look wrong to the scorer:

  * Contractions: "don't" vs "do not". The matcher aligns token counts, so
    contractions are EXPANDED on both sides ("don't" -> "do not"), always, which
    keeps target/said/display token counts consistent. Possessives are untouched.
  * Numbers: speech recognition writes "3", "21st", "1,000"; generated text may spell
    them out. Digits are spelled out on both sides ("21st" -> "twenty first").
  * US/UK spelling: both are correct English, so UK forms are canonicalized to US
    ("colour" -> "color") in every mode, dictation included.
  * Homophones: one-token sound-alikes (to/too/two, there/their, …) merge only for
    speaking (phonetic=True); dictation keeps them distinct. Pairs where one side is
    a contraction (its/it's, your/you're) can't merge — the contraction expands to
    two tokens.

French-only machinery (silent plural -s, liaison marks, noun/adj tagging) is a no-op.
Feedback prompts write their explanations in French for French-speaking learners.
"""

import re
import unicodedata

CODE = "en"


# ── Contractions (text level, always) ─────────────────────────────────────────
# Ambiguous 's / 'd are expanded to one canonical reading (is / would); both sides
# go through the same rule, so matching stays consistent even when the literal
# meaning is "has" / "had".
_CONTRACTION_SPECIAL = [
    (r"\bcan't\b", "can not"),
    (r"\bcannot\b", "can not"),
    (r"\bwon't\b", "will not"),
    (r"\bshan't\b", "shall not"),
    (r"\bain't\b", "aint"),  # no single expansion; keep it one token
    (r"\blet's\b", "let us"),
    (r"\bi'm\b", "i am"),
]
_S_WORDS = r"he|she|it|that|what|who|where|when|why|how|there|here"
_D_WORDS = r"i|you|he|she|we|they|it|that|who|there"
_LL_WORDS = r"i|you|he|she|we|they|it|that|there|who|what"
_RE_WORDS = r"you|we|they|who|what|there"
_VE_WORDS = r"i|you|we|they|who|could|should|would|might|must"
_CONTRACTION_RULES = [(re.compile(p), r) for p, r in _CONTRACTION_SPECIAL] + [
    (re.compile(r"\b(\w+)n't\b"), r"\1 not"),
    (re.compile(r"\b({})'s\b".format(_S_WORDS)), r"\1 is"),
    (re.compile(r"\b({})'d\b".format(_D_WORDS)), r"\1 would"),
    (re.compile(r"\b({})'ll\b".format(_LL_WORDS)), r"\1 will"),
    (re.compile(r"\b({})'re\b".format(_RE_WORDS)), r"\1 are"),
    (re.compile(r"\b({})'ve\b".format(_VE_WORDS)), r"\1 have"),
]


def expand_contractions(t: str) -> str:
    for pat, rep in _CONTRACTION_RULES:
        t = pat.sub(rep, t)
    return t


# ── Numbers (text level, always) ──────────────────────────────────────────────
_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_ORDINAL_IRREGULAR = {"one": "first", "two": "second", "three": "third", "five": "fifth",
                      "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}


def _cardinal(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        rest = n % 100
        return _ONES[n // 100] + " hundred" + ("" if rest == 0 else " " + _cardinal(rest))
    if n < 1000000:
        rest = n % 1000
        return _cardinal(n // 1000) + " thousand" + ("" if rest == 0 else " " + _cardinal(rest))
    return str(n)  # out of range: leave as digits (both sides still match)


def _ordinal(n: int) -> str:
    words = _cardinal(n).split(" ")
    last = words[-1]
    if last in _ORDINAL_IRREGULAR:
        last = _ORDINAL_IRREGULAR[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last = last + "th"
    return " ".join(words[:-1] + [last])


_THOUSANDS_SEP_RE = re.compile(r"(?<=\d),(?=\d{3}\b)")
_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_DECIMAL_RE = re.compile(r"(?<=\d)\.(?=\d)")
_ORDINAL_NUM_RE = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b")
_CARDINAL_RE = re.compile(r"\b\d+\b")


def spell_numbers(t: str) -> str:
    t = _THOUSANDS_SEP_RE.sub("", t)
    t = _TIME_RE.sub(r"\1 \2", t)
    t = _DECIMAL_RE.sub(" point ", t)
    t = t.replace("%", " percent").replace("&", " and ")
    t = _ORDINAL_NUM_RE.sub(lambda m: _ordinal(int(m.group(1))), t)
    return _CARDINAL_RE.sub(lambda m: _cardinal(int(m.group(0))), t)


# ── US/UK spelling (word level, always) ───────────────────────────────────────
# Curated rather than rule-based: blanket -our/-ise/-re rewrites would corrupt
# hour/four/your, rise/promise/exercise, more/where. Keys are post-normalization
# tokens (lowercase, apostrophes stripped). Extend as UK forms surface.
def _forms(uk: str, us: str, suffixes):
    return {uk + s: us + s for s in suffixes}


_UK_TO_US = {}
for _stem in ["colour", "flavour", "favour", "honour", "humour", "labour", "neighbour",
              "behaviour", "harbour", "rumour", "savour", "vapour", "vigour", "endeavour",
              "parlour", "armour", "odour", "tumour"]:
    _UK_TO_US.update(_forms(_stem, _stem[:-2] + "r",
                            ["", "s", "ed", "ing", "ful", "less", "able", "ite", "ites",
                             "hood", "hoods", "ly", "er", "ers", "al"]))
for _stem in ["organ", "real", "recogn", "apolog", "critic", "special", "priorit",
              "emphas", "summar", "memor", "minim", "maxim", "custom", "final", "util",
              "categor", "character", "author", "civil", "modern", "standard", "sympath",
              "visual", "global", "normal", "optim", "familiar", "personal", "social",
              "legal", "central", "stabil", "fertil", "hospital", "internal", "mobil",
              "neutral", "rational", "subsid", "symbol", "synchron", "tantal", "harmon",
              "agon", "patron", "scrutin", "caramel", "vandal", "general", "industrial",
              "capital", "commercial", "digit", "energ", "econom", "idol", "local"]:
    _UK_TO_US.update(_forms(_stem + "is", _stem + "iz",
                            ["e", "es", "ed", "ing", "er", "ers", "ation", "ations", "able"]))
for _stem in ["analys", "paralys", "catalys"]:
    _UK_TO_US.update(_forms(_stem, _stem[:-1] + "z", ["e", "es", "ed", "ing"]))
for _uk, _us in [("centre", "center"), ("theatre", "theater"), ("metre", "meter"),
                 ("litre", "liter"), ("fibre", "fiber"), ("calibre", "caliber"),
                 ("sombre", "somber"), ("spectre", "specter"), ("lustre", "luster"),
                 ("kilometre", "kilometer"), ("centimetre", "centimeter"),
                 ("millimetre", "millimeter")]:
    _UK_TO_US.update(_forms(_uk, _us, [""]))
    _UK_TO_US[_uk + "s"] = _us + "s"
for _uk, _us in [("catalogue", "catalog"), ("dialogue", "dialog"), ("analogue", "analog"),
                 ("monologue", "monolog"), ("prologue", "prolog"), ("epilogue", "epilog")]:
    _UK_TO_US.update({_uk: _us, _uk + "s": _us + "s"})
for _base in ["travel", "cancel", "label", "model", "fuel", "signal", "level", "total",
              "counsel", "dial", "duel", "equal", "marvel", "quarrel", "tunnel", "channel",
              "shovel", "snorkel", "grovel", "jewel", "rival", "panel", "pedal", "spiral"]:
    _UK_TO_US.update({_base + "led": _base + "ed", _base + "ling": _base + "ing",
                      _base + "ler": _base + "er", _base + "lers": _base + "ers"})
_UK_TO_US.update({
    "jewellery": "jewelry", "grey": "gray", "greys": "grays", "tyre": "tire", "tyres": "tires",
    "programme": "program", "programmes": "programs", "cheque": "check", "cheques": "checks",
    "defence": "defense", "offence": "offense", "licence": "license", "pretence": "pretense",
    "practise": "practice", "practised": "practiced", "practising": "practicing",
    "mum": "mom", "mums": "moms", "aluminium": "aluminum", "plough": "plow",
    "draught": "draft", "draughts": "drafts", "storey": "story", "storeys": "stories",
    "pyjamas": "pajamas", "enrol": "enroll", "enrolment": "enrollment", "fulfil": "fulfill",
    "fulfilment": "fulfillment", "skilful": "skillful", "wilful": "willful",
    "ageing": "aging", "judgement": "judgment", "kerb": "curb", "mould": "mold",
    "mouldy": "moldy", "moustache": "mustache", "sceptical": "skeptical", "sceptic": "skeptic",
    "manoeuvre": "maneuver", "manoeuvres": "maneuvers", "aeroplane": "airplane",
    "aeroplanes": "airplanes", "doughnut": "donut", "doughnuts": "donuts",
    "cosy": "cozy", "whilst": "while", "amongst": "among", "learnt": "learned",
    "burnt": "burned", "dreamt": "dreamed", "spelt": "spelled", "spoilt": "spoiled",
    "smelt": "smelled", "leapt": "leaped", "towards": "toward", "afterwards": "afterward",
    "focussed": "focused", "focussing": "focusing", "benefitted": "benefited",
    "benefitting": "benefiting", "paediatric": "pediatric", "encyclopaedia": "encyclopedia",
    "oestrogen": "estrogen", "anaemia": "anemia", "anaesthetic": "anesthetic",
})


def canonicalize_spelling(words: list) -> list:
    return [_UK_TO_US.get(w, w) for w in words]


# ── Speaking-only homophones (word level, phonetic=True) ──────────────────────
# Sets of one-token words that are pronounced identically in both US and UK
# English. Each maps to one canonical member. Only true homophones — near-rhymes
# (then/than, accept/except) would inflate scores.
_HOMOPHONE_SETS = [
    ("to", "too", "two"), ("there", "their"), ("for", "four", "fore"),
    ("by", "buy", "bye"), ("right", "write", "rite"), ("know", "no"), ("new", "knew"),
    ("hear", "here"), ("see", "sea"), ("one", "won"), ("eight", "ate"),
    ("weather", "whether"), ("would", "wood"), ("week", "weak"), ("meet", "meat"),
    ("where", "wear", "ware"), ("hour", "our"), ("peace", "piece"), ("whole", "hole"),
    ("wait", "weight"), ("way", "weigh"), ("sun", "son"), ("flour", "flower"),
    ("mail", "male"), ("sale", "sail"), ("tail", "tale"), ("plain", "plane"),
    ("break", "brake"), ("steal", "steel"), ("pair", "pear", "pare"), ("bare", "bear"),
    ("fair", "fare"), ("hair", "hare"), ("stair", "stare"), ("dear", "deer"),
    ("road", "rode"), ("blue", "blew"), ("threw", "through"), ("made", "maid"),
    ("sent", "cent", "scent"), ("allowed", "aloud"), ("which", "witch"),
    ("knight", "night"), ("knot", "not"), ("knows", "nose"),
    ("passed", "past"), ("principal", "principle"), ("stationary", "stationery"),
    ("seen", "scene"), ("sight", "site", "cite"), ("some", "sum"), ("wore", "war"),
    ("heard", "herd"), ("idle", "idol"), ("in", "inn"), ("jeans", "genes"),
    ("medal", "meddle"), ("missed", "mist"), ("morning", "mourning"), ("pause", "paws"),
    ("rain", "reign", "rein"), ("role", "roll"), ("sole", "soul"),
    ("tide", "tied"), ("toe", "tow"), ("waste", "waist"),
]
_HOMOPHONES = {}
for _group in _HOMOPHONE_SETS:
    for _w in _group:
        _HOMOPHONES.setdefault(_w, _group[0])


def normalize_homophones(words: list) -> list:
    return [_HOMOPHONES.get(w, w) for w in words]


# ── Profile surface ───────────────────────────────────────────────────────────
def normalize_text(t: str) -> str:
    """Text-level English normalization, applied after lowercasing/apostrophe
    unification and before tokenizing: expand contractions, spell out numbers."""
    return spell_numbers(expand_contractions(t))


def strip_accents(words: list) -> list:
    """Accents on English loanwords are optional spelling (café/cafe, naïve/naive,
    fiancé/fiance), so they never count — in any mode, dictation included."""
    return ["".join(c for c in unicodedata.normalize("NFKD", w) if not unicodedata.combining(c))
            for w in words]


def normalize_words(words: list, phonetic: bool = False) -> list:
    """Token-level English normalization: accents and US/UK spelling always; with
    ``phonetic`` (speaking exercises) also merge one-token homophones."""
    words = canonicalize_spelling(strip_accents(words))
    if phonetic:
        words = normalize_homophones(words)
    return words


def strip_plural(word: str, noun_adj_set) -> str:
    """No-op: English plural -s is pronounced, so it must still count."""
    return word


def tag_nouns_adjs(text: str) -> list:
    """No-op: plural-stripping is French-only, so English needs no noun/adj tags."""
    return []


def detect_links(text: str) -> str:
    """No-op: liaison/enchaînement marks are French-only."""
    return text


ANALYSIS_SYSTEM = """You are analyzing an English shadowing exercise result for a French-speaking learner of English.

The student was asked to repeat an English phrase exactly. You are given:
- The target phrase (what they should have said)
- The transcription (what speech recognition captured)
- A list of mismatched word pairs (target_word vs transcribed_word)

WRITE ALL EXPLANATIONS IN FRENCH. The target words themselves and their IPA stay in English.

For each mismatch, provide:
1. A pronunciation tip in EXACTLY this format: "<target_word> /<IPA>/ — <body-mechanics cue in French>"
   - Write the target word exactly as given, then its English IPA transcription between slashes
   - After the em-dash: one short body-mechanics cue in French — tongue placement, lip shape, vowel length, word stress, silent letter, etc.
   - Focus on the sounds French speakers typically struggle with: "th" /θ/ /ð/, the "h" that must be aspirated, short vs long vowels (ship/sheep), the schwa /ə/ in unstressed syllables, word stress, the English "r" /ɹ/, and "-ed" endings (/t/, /d/, /ɪd/)
   - Examples:
     "think /θɪŋk/ — pointe de la langue entre les dents, soufflez doucement ; pas de « s »"
     "house /haʊs/ — le « h » s'entend : expirez comme sur une vitre froide"
     "ship /ʃɪp/ — voyelle courte et relâchée, plus brève que dans « sheep »"
     "walked /wɔːkt/ — le « -ed » se prononce /t/, pas de syllabe en plus"
   - Max 20 words total. Never deviate from this format.
2. Whether this is a grammar/tense distinction (e.g. walk vs walked, do vs does)
3. If it IS a grammar distinction, a one-sentence grammar note in French

IMPORTANT: Return exactly one feedback entry for EVERY mismatch in the list — same count, same order. Never skip, merge, or omit a mismatch, even if the transcribed word looks unrelated or nonsensical.

Return ONLY valid JSON in this exact shape:
{
  "feedback": [
    {
      "target_word": "thought",
      "said": "taught",
      "tip": "thought /θɔːt/ — pointe de la langue entre les dents pour le « th », pas un « t »",
      "is_grammar": false,
      "grammar_note": ""
    }
  ]
}

If there are no mismatches to analyze, return: {"feedback": []}"""


DICTATION_ANALYSIS_SYSTEM = """You are analyzing an English dictation exercise for a French-speaking learner of English. The student listened to a spoken sentence and typed what they heard.

WRITE ALL NOTES IN FRENCH. The target words themselves and their IPA stay in English.

You receive:
- The target sentence (what was spoken)
- What the student typed
- A list of mismatched word pairs: {target_word, typed}

For each mismatch, provide feedback covering TWO angles where relevant:

1. PHONETIC NOTE — why might a careful listener write the wrong word?
   Explain in French the acoustic difference between what was said and what they wrote.
   Format: "<target_word> /<IPA>/ — <what to listen for, in French, max 20 words>"
   Examples:
     "walked /wɔːkt/ — le « -ed » final est un /t/ très bref, facile à ne pas entendre"
     "can't /kɑːnt/ — voyelle longue et « t » final ; « can » /kən/ est réduit et rapide"
     "their /ðeə/ — même son que « there » : c'est le sens qui décide de l'orthographe"

2. GRAMMAR NOTE — if the error reveals a grammar gap, one short sentence in French explaining the rule.
   If the error is purely phonetic/spelling, leave this empty string.
   Examples:
     "Prétérit : les verbes réguliers prennent « -ed » — walked, pas walk."
     "Troisième personne du singulier : « he works », avec un « s »."

Classify each error as one of: tense | vocabulary | spelling | phonetic

Return ONLY valid JSON:
{
  "feedback": [
    {
      "target_word": "walked",
      "typed": "walk",
      "phonetic_note": "walked /wɔːkt/ — le « -ed » final est un /t/ très bref, facile à ne pas entendre",
      "grammar_note": "Prétérit : les verbes réguliers prennent « -ed » — walked, pas walk.",
      "error_type": "tense"
    }
  ]
}

If there are no mismatches, return: {"feedback": []}"""
