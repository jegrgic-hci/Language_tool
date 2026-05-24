import spacy

_nlp = None

def _get_nlp():
    global _nlp
    if _nlp is None:
        _nlp = spacy.load("fr_core_news_sm")
    return _nlp

def tag_nouns_adjs(text: str) -> list:
    """Return every noun and adjective token from text, exactly as written."""
    nlp = _get_nlp()
    doc = nlp(text)
    return [token.text for token in doc if token.pos_ in ("NOUN", "ADJ")]
