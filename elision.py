import re

FRENCH_ELISION_RULES = [
    # === 1. je + verb ===
    (r'\bje ai\b', "j'ai"),
    (r'\bje aime\b', "j'aime"),
    (r'\bje espère\b', "j'espère"),
    (r'\bje habite\b', "j'habite"),
    (r'\bje écoute\b', "j'écoute"),
    (r'\bje étudie\b', "j'étudie"),
    (r'\bje ([aeéèêëioôuùûüœæh]\w*)', r"j'\1"),

    # === 2. ne + verb ===
    (r'\bne est\b', "n'est"),
    (r'\bne ai\b', "n'ai"),
    (r'\bne as\b', "n'as"),
    (r'\bne a\b', "n'a"),
    (r'\bne avons\b', "n'avons"),
    (r'\bne ([aeéèêëioôuùûüœæh]\w*)', r"n'\1"),

    # === 3. ce + verb ===
    (r'\bce est\b', "c'est"),
    (r'\bce était\b', "c'était"),
    (r'\bce ([aeéèêëioôuùûüœæh]\w*)', r"c'\1"),

    # === 4. que + word ===
    (r'\bque il\b', "qu'il"),
    (r'\bque elle\b', "qu'elle"),
    (r'\bque on\b', "qu'on"),
    (r'\bque ([aeéèêëioôuùûüœæh]\w*)', r"qu'\1"),

    # === 5. Articles ===
    (r'\ble ([aeéèêëioôuùûüœæh]\w*)', r"l'\1"),
    (r'\bla ([aeéèêëioôuùûüœæh]\w*)', r"l'\1"),

    # === 6. me/te/se + vowel ===
    (r'\bme ([aeéèêëioôuùûüœæh]\w*)', r"m'\1"),
    (r'\bte ([aeéèêëioôuùûüœæh]\w*)', r"t'\1"),
    (r'\bse ([aeéèêëioôuùûüœæh]\w*)', r"s'\1"),

    # === 6b. tu + avoir/être (colloquial spoken: tu as → t'as) ===
    (r'\btu (as|avais|avait|auras|aura|aurais|aurait|aurions|auriez|auraient|eu'
     r'|es|étais|était|étions|étiez|étaient|seras|sera|serais|serait|serions|seriez|seraient|été)\b',
     r"t'\1"),

    # === 7. de + vowel ===
    (r'\bde accord\b', "d'accord"),
    (r'\bde ici\b', "d'ici"),
    (r'\bde autre\b', "d'autre"),
    (r'\bde ([aeéèêëioôuùûüœæh]\w*)', r"d'\1"),

    # === 8. Pronouns + y/en ===
    (r'\bje y\b', "j'y"),
    (r'\bje en\b', "j'en"),
    (r'\bme y\b', "m'y"),
    (r'\bte y\b', "t'y"),
    (r'\bse y\b', "s'y"),

    # === 9. Subordinating conjunctions ===
    (r'\bjusqu à\b', "jusqu'à"),
    (r'\blorsque il\b', "lorsqu'il"),
    (r'\blorsque elle\b', "lorsqu'elle"),
    (r'\bpuisque il\b', "puisqu'il"),
    (r'\bpuisque elle\b', "puisqu'elle"),
    (r'\bquoique il\b', "quoiqu'il"),

    # === 10. Common phrases ===
    (r'\bce n est pas\b', "ce n'est pas"),
    (r'\bje ne sais pas\b', "je ne sais pas"),
]


def normalize_french(text: str) -> str:
    for pattern, replacement in FRENCH_ELISION_RULES:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


# Pairs that sound identical but differ in spelling/accent.
# Applied word-by-word during scoring so speech-API output matches written targets.
FRENCH_HOMOPHONES = {
    "j'aie": "j'ai",
    "dès": "des",
    "à": "a",
    "où": "ou",
    "là": "la",
    "dû": "du",
    "sûr": "sur",
    "mûr": "mur",
    "crû": "cru",
    "jeûne": "jeune",
    # imparfait 3rd-person: singular and plural are pronounced identically [etɛ]
    "étaient": "était",
    "avaient": "avait",
    "allaient": "allait",
    "faisaient": "faisait",
    "venaient": "venait",
    "prenaient": "prenait",
    "pouvaient": "pouvait",
    "voulaient": "voulait",
    "savaient": "savait",
    "devaient": "devait",
}


def normalize_homophones(words: list) -> list:
    return [FRENCH_HOMOPHONES.get(w, w) for w in words]


# Words where terminal -s IS pronounced — exempt from silent-s stripping.
_SILENT_S_EXCEPTIONS = frozenset({
    "fils", "bus", "ours", "mars", "sens", "os", "vis", "plus",
    "terminus", "campus", "chorus", "corpus", "hiatus", "lapsus",
    "nexus", "syllabus", "prospectus", "cactus", "virus", "bonus",
    "radius", "focus", "humus", "mucus", "blocus",
})


def strip_terminal_s(word: str, noun_adj_set: set) -> str:
    """Strip a silent terminal -s from nouns/adjectives during scoring normalization.

    Only strips when the base form (word minus -s) is in noun_adj_set, so the
    rule is phrase-specific rather than applied blindly to every -s word.
    """
    if word.endswith("s") and word not in _SILENT_S_EXCEPTIONS:
        base = word[:-1]
        if base in noun_adj_set:
            return base
    return word

