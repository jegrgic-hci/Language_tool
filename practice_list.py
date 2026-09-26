import os
import json
import uuid as _uuid
from pathlib import Path
from datetime import datetime
from typing import Optional

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data"))
PRACTICE_LIST_FILE = DATA_DIR / "practice_list.json"


# Each entry carries its study language ("fr" / "en"); entries saved before the
# English list existed have no "lang" and are French.
def _lang_of(item: dict) -> str:
    return item.get("lang") or "fr"


def _is_word(item: dict, word: str, lang: str) -> bool:
    return (item.get("type", "word") == "word" and _lang_of(item) == lang
            and item["word"].lower() == word.lower())


def _load() -> list[dict]:
    if not PRACTICE_LIST_FILE.exists():
        return []
    try:
        items = json.loads(PRACTICE_LIST_FILE.read_text(encoding="utf-8"))
        # Backfill id and type on legacy entries
        changed = False
        for item in items:
            if "id" not in item:
                item["id"] = str(_uuid.uuid4())
                changed = True
            if "type" not in item:
                item["type"] = "word"
                changed = True
        if changed:
            _save(items)
        return items
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    PRACTICE_LIST_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def get_all(lang: str = "fr") -> list[dict]:
    return [i for i in _load() if _lang_of(i) == lang]


def add_word(word: str, tip: str, source_phrase: Optional[str] = None, article: Optional[str] = None,
             entry_type: str = "word", lang: str = "fr") -> dict:
    items = _load()
    # For words, update in place if already exists
    if entry_type == "word":
        for item in items:
            if _is_word(item, word, lang):
                item["tip"] = tip
                if source_phrase:
                    item["source_phrase"] = source_phrase
                if article is not None:
                    item["article"] = article
                item["updated_at"] = datetime.utcnow().isoformat()
                _save(items)
                return item
    entry = {
        "id": str(_uuid.uuid4()),
        "type": entry_type,
        "word": word,
        "tip": tip,
        "source_phrase": source_phrase or "",
        "article": article or "",
        "lang": lang,
        "added_at": datetime.utcnow().isoformat(),
    }
    items.append(entry)
    _save(items)
    return entry


def get_word(word: str, lang: str = "fr") -> Optional[dict]:
    for item in _load():
        if _is_word(item, word, lang):
            return item
    return None


def update_carrier(word: str, carrier: str, lang: str = "fr") -> None:
    items = _load()
    for item in items:
        if _is_word(item, word, lang):
            item["carrier"] = carrier
            _save(items)
            return


def remove_word(word: str, lang: str = "fr") -> bool:
    items = _load()
    before = len(items)
    items = [i for i in items if not _is_word(i, word, lang)]
    if len(items) < before:
        _save(items)
        return True
    return False


def remove_entry(entry_id: str) -> bool:
    items = _load()
    before = len(items)
    items = [i for i in items if i.get("id") != entry_id]
    if len(items) < before:
        _save(items)
        return True
    return False
