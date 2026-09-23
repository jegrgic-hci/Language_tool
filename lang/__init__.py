"""
Study-language profiles.

Each supported study language is a module in this package (``lang/fr.py``, …) that
exposes the same surface: ``CODE``, ``normalize_text``, ``normalize_words``,
``strip_plural``, ``tag_nouns_adjs``, ``detect_links``, ``ANALYSIS_SYSTEM`` and
``DICTATION_ANALYSIS_SYSTEM``. Shared code asks for a profile with ``get(lang)``
and never imports a language module directly.

The language is always passed in explicitly (default "fr"), never read from a
global, so one process can serve learners of different languages side by side.
"""

import importlib

SUPPORTED = ("fr", "en")


def get(lang: str = "fr"):
    """The profile module for ``lang``. Raises on an unsupported language rather
    than falling back to French."""
    if lang not in SUPPORTED:
        raise ValueError("Unsupported study language: {!r}".format(lang))
    return importlib.import_module("lang." + lang)
