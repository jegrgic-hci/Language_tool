"""
French phonetic category lookup, backed by Lexique383.

Loads data/Lexique383.tsv once at import time. Maps each French word to
the set of phonological categories that appear in its pronunciation.

Lexique383 single-character phoneme codes used here:
  Nasals : @ = [ɑ̃]  § = [ɔ̃]  5 = [ɛ̃]  1 = [œ̃]
  U-sound: y = [y]
  EU     : 2 = [ø]  9 = [œ]
"""

import csv
from pathlib import Path
from typing import Dict, FrozenSet, List

_DATA_PATH = Path(__file__).parent / "data" / "Lexique383.tsv"

_NASAL_CHARS = frozenset("@§51")   # ɑ̃  ɔ̃  ɛ̃  œ̃
_U_CHARS     = frozenset("y")      # [y]
_EU_CHARS    = frozenset("29")     # [ø]  [œ]

# ortho → frozenset of category labels
_LOOKUP: Dict[str, FrozenSet[str]] = {}


def _build_lookup() -> None:
    if not _DATA_PATH.exists():
        return
    acc: Dict[str, set] = {}
    with open(_DATA_PATH, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            word = row["ortho"].lower()
            phon = row.get("phon", "")
            cats: set = set()
            if any(c in phon for c in _NASAL_CHARS):
                cats.add("nasal")
            if any(c in phon for c in _U_CHARS):
                cats.add("u_sound")
            if any(c in phon for c in _EU_CHARS):
                cats.add("eu_sound")
            if cats:
                if word in acc:
                    acc[word] |= cats
                else:
                    acc[word] = cats
    for word, cats in acc.items():
        _LOOKUP[word] = frozenset(cats)


_build_lookup()


def get_phonetic_categories(word: str) -> List[str]:
    """Return sorted list of phonetic category labels for a French word, or []."""
    return sorted(_LOOKUP.get(word.lower(), frozenset()))
