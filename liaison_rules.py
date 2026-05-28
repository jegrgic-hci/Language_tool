"""
Mandatory liaison (‿ U+203F) and enchaînement (⁀ U+2040) detection for French phrases.

Liaison     — normally-silent consonant becomes voiced before a vowel-initial word.
Enchaînement — always-pronounced consonant resyllabifies onto the next vowel-initial word.
"""
import re
from pos_tagger import _get_nlp

_VOWEL_LETTERS = frozenset('aeiouéèêëàâùûüîïôöœæy')

# H-aspiré words — block linking even though they start with 'h'
H_ASPIRE = frozenset({
    'haie', 'haies', 'haine', 'haines', 'halte', 'hameau', 'hameaux',
    'hamster', 'hanche', 'hanches', 'hangar', 'hangars',
    'haricot', 'haricots', 'harpe', 'harpes', 'hasard', 'hasards',
    'hâte', 'hausse', 'hausses', 'haut', 'hauts', 'haute', 'hautes',
    'hibou', 'hiboux', 'hiérarchie', 'hockey',
    'homard', 'homards', 'honte', 'hontes', 'hoquet',
    'horde', 'hordes', 'hors', 'housse', 'housses',
    'hussard', 'hussards',
})

# Determiners with silent final consonant → mandatory liaison before vowel
_LIAISON_DETERMINERS = frozenset({
    'les', 'des', 'mes', 'tes', 'ses', 'nos', 'vos', 'leurs', 'ces', 'aux',
    'quels', 'quelles', 'quelques',
    'un',           # un ami → /œ̃.n‿a.mi/
    'mon', 'ton', 'son',   # mon ami → /mɔ̃.n‿a.mi/
})

# Clitic pronouns and short adverbs with silent final consonant
_LIAISON_PRONOUNS = frozenset({
    'nous', 'vous', 'ils', 'elles', 'on',
    'en', 'y',
})

# Prepositions
_LIAISON_PREPOSITIONS = frozenset({
    'dans', 'sans', 'sous', 'chez', 'dès', 'depuis', 'vers',
    'quand',   # quand il → /kɑ̃.til/
})

# Cardinal numbers
_LIAISON_NUMBERS = frozenset({
    'deux', 'trois', 'six', 'dix', 'vingt', 'cent',
})

# Common être/avoir verb forms with silent final consonant
_LIAISON_VERBS = frozenset({
    'est', 'sont', 'ont', 'était', 'étaient', 'avait', 'avaient',
    'fait', 'peut', 'doit', 'vient', 'tient',
})

# Pre-nominal adjectives — only liaison when spaCy confirms position before noun
_LIAISON_ADJECTIVES = frozenset({
    'petit', 'petits',
    'grand', 'grands',
    'bon', 'bons',
    'gros',
    'mauvais',
    'vieux', 'vieil',
    'beau', 'beaux', 'bel',
    'tout', 'tous',
    'premier', 'premiers',
    'dernier', 'derniers',
})

LIAISON_WORDS = (
    _LIAISON_DETERMINERS | _LIAISON_PRONOUNS |
    _LIAISON_PREPOSITIONS | _LIAISON_NUMBERS |
    _LIAISON_VERBS | _LIAISON_ADJECTIVES
)

# Words with ALWAYS-PRONOUNCED final consonants → enchaînement before vowel
ENCHAÎNEMENT_WORDS = frozenset({
    # -r (not -er infinitives where r is silent)
    'pour', 'sur', 'par', 'leur', 'soir', 'jour', 'jours', 'tour', 'tours',
    'cœur', 'fleur', 'fleurs', 'peur', 'heure', 'heures',
    'mer', 'mers', 'air', 'airs', 'chair', 'car', 'or',
    'hier', 'cher', 'mur', 'pur', 'dur',
    'bonjour', 'bonsoir',
    'couleur', 'couleurs', 'valeur', 'valeurs', 'chaleur', 'douleur',
    'longueur', 'largeur', 'hauteur', 'profondeur',
    'docteur', 'professeur', 'directeur', 'acteur', 'secteur',
    'futur', 'obscur', 'pur',
    # -l
    'il', 'elle', 'quel', 'quelle', 'quels', 'quelles',
    'tel', 'telle', 'tels', 'telles',
    'bel', 'belle', 'belles',
    'mal', 'bal', 'col', 'gel', 'sel', 'sol', 'vol', 'bol',
    'général', 'naturel', 'formel', 'culturel', 'officiel', 'essentiel',
    'principal', 'normal', 'total', 'global', 'local', 'final',
    # -f
    'chef', 'chefs', 'bref',
    'vif', 'vifs', 'actif', 'actifs', 'naïf',
    'positif', 'négatif', 'sportif', 'motif',
    # -c (pronounced /k/)
    'avec', 'sec', 'bec', 'lac', 'sac', 'choc',
})


def _starts_with_vowel_sound(word: str) -> bool:
    """True if word begins with a vowel or h-muet (not h-aspiré)."""
    w = word.lower().lstrip("\"«'‘’“”")
    if not w:
        return False
    c = w[0]
    if c in _VOWEL_LETTERS:
        return True
    if c == 'h':
        return w not in H_ASPIRE
    return False


def detect_links(phrase: str) -> str:
    """
    Return phrase with ‿ (liaison) or ⁀ (enchaînement) replacing the space
    between word pairs where mandatory linking applies. Strips any existing markers first.
    """
    # Strip any markers Mistral may have inserted
    clean = re.sub(r'[‿⁀_]', ' ', phrase)
    clean = re.sub(r'\s+', ' ', clean).strip()

    words = clean.split()
    if len(words) < 2:
        return clean

    # Identify pre-nominal adjectives via spaCy dependency parse
    nlp = _get_nlp()
    doc = nlp(clean)
    prenominal_adjs: set = set()
    for token in doc:
        if (token.pos_ == 'ADJ'
                and token.head.pos_ in ('NOUN', 'PROPN')
                and token.i < token.head.i):
            prenominal_adjs.add(token.lower_)

    out: list = []
    for i, word in enumerate(words):
        out.append(word)
        if i + 1 >= len(words):
            break

        next_word = words[i + 1]
        nw = next_word.lstrip("\"«'‘’“”")

        if not _starts_with_vowel_sound(nw):
            out.append(' ')
            continue

        w = word.lower().rstrip('.,!?;:»’')

        if w in _LIAISON_ADJECTIVES:
            marker = '‿' if w in prenominal_adjs else ' '
        elif w in LIAISON_WORDS:
            marker = '‿'
        elif w in ENCHAÎNEMENT_WORDS:
            marker = '⁀'
        else:
            marker = ' '

        out.append(marker)

    return ''.join(out)
