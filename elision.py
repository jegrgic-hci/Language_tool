import re

# === Number normalization ===
# Maps digit strings to their French spoken equivalents.
# Used so target text like "3" matches STT output "trois" during scoring.

def _build_number_words() -> dict:
    ones = [
        "zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept",
        "huit", "neuf", "dix", "onze", "douze", "treize", "quatorze",
        "quinze", "seize", "dix-sept", "dix-huit", "dix-neuf",
    ]
    tens = ["", "", "vingt", "trente", "quarante", "cinquante", "soixante"]

    def word(n):
        if n < 20:
            return ones[n]
        if n < 70:
            t, o = divmod(n, 10)
            if o == 0:
                return tens[t] + ("s" if t == 8 else "")
            if o == 1 and t != 8:
                return tens[t] + " et un"
            return tens[t] + "-" + ones[o]
        if n < 80:
            o = n - 60
            if o == 11:
                return "soixante et onze"
            return "soixante-" + ones[o]
        if n < 90:
            o = n - 80
            return "quatre-vingts" if o == 0 else "quatre-vingt-" + ones[o]
        if n == 100:
            return "cent"
        # 90-99
        return "quatre-vingt-" + ones[n - 80]

    result = {str(i): word(i) for i in range(101)}
    result["1000"] = "mille"
    return result

FRENCH_NUMBER_WORDS = _build_number_words()


def normalize_numbers(text: str) -> str:
    """Replace digit sequences with their French word equivalents."""
    def replace(m):
        return FRENCH_NUMBER_WORDS.get(m.group(), m.group())
    return re.sub(r'\b\d+\b', replace, text)


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


# Hyphenated loanwords where STT returns a single merged token.
# Must be applied before the hyphen→space replacement in _normalize().
FRENCH_HYPHEN_MERGES = [
    "week-end",
    "check-in",
    "check-out",
    "fast-food",
    "self-service",
    "t-shirt",
    "wi-fi",
    "cd-rom",
    "dvd-rom",
    "pop-corn",
    "milk-shake",
    "best-of",
    "far-west",
    "free-lance",
    "knock-out",
    "play-back",
    "stand-by",
]


def normalize_french(text: str) -> str:
    text = normalize_numbers(text)
    # î→i and û→u: circumflex on these vowels is purely orthographic (1990 reform);
    # STT never produces the circumflex form, so strip it before scoring.
    text = text.replace("î", "i").replace("Î", "I")
    text = text.replace("û", "u").replace("Û", "U")
    # Merge hyphenated loanwords before the hyphen→space step in _normalize()
    # so "week-end" scores as one token matching STT output "weekend".
    for word in FRENCH_HYPHEN_MERGES:
        text = re.sub(re.escape(word), word.replace("-", ""), text, flags=re.IGNORECASE)
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


# Vowels that can precede a silent feminine -e.
_FR_VOWELS = frozenset("aeiouéèêëàâîïôùûüæœ")


def normalize_mute_feminine_e(words: list) -> list:
    """Strip a silent final -e when preceded by a vowel (vraie→vrai, jolie→joli, gaie→gai).

    Applied symmetrically to both target and transcription so the scoring
    comparison stays valid even when target words also end in -e.
    """
    result = []
    for w in words:
        if len(w) >= 2 and w[-1] == "e" and w[-2] in _FR_VOWELS:
            result.append(w[:-1])
        else:
            result.append(w)
    return result


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

