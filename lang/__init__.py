"""
Study-language profiles.

Each supported study language is a module in this package (``lang/fr.py``, …) that
exposes the same surface: ``CODE``, ``normalize_text``, ``normalize_words``,
``strip_plural``, ``tag_nouns_adjs``, ``detect_links``, ``ANALYSIS_SYSTEM`` and
``DICTATION_ANALYSIS_SYSTEM``. Shared code asks for a profile with ``get(lang)``
and never imports a language module directly.

The language is always passed in explicitly (default "fr"), never read from a
global, so one process can serve learners of different languages side by side.

A *locale* is resolved per item from the learner's choice (Français, or English
with an accent setting of US / UK / Both — Both picks one per item): it selects the
voices, the speech-recognition locale and the content bank, while its *language*
selects the scoring profile — en-US and en-GB share the "en" rules.
"""

import importlib

SUPPORTED = ("fr", "en")

# locale -> language + the edge-tts voice used when Chirp3-HD is unavailable.
LOCALES = {
    "fr-FR": {"lang": "fr", "edge_voice": "fr-FR-DeniseNeural"},
    "en-US": {"lang": "en", "edge_voice": "en-US-JennyNeural"},
    "en-GB": {"lang": "en", "edge_voice": "en-GB-SoniaNeural"},
}
DEFAULT_LOCALE = "fr-FR"


def lang_of(locale: str) -> str:
    """The study language for a learner-facing locale. Raises on an unknown locale."""
    try:
        return LOCALES[locale]["lang"]
    except KeyError:
        raise ValueError("Unsupported locale: {!r}".format(locale))


def get(lang: str = "fr"):
    """The profile module for ``lang``. Raises on an unsupported language rather
    than falling back to French."""
    if lang not in SUPPORTED:
        raise ValueError("Unsupported study language: {!r}".format(lang))
    return importlib.import_module("lang." + lang)
