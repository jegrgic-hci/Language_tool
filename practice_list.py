import os
import json
import uuid as _uuid
from pathlib import Path
from datetime import datetime
from typing import Optional

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data"))
PRACTICE_LIST_FILE = DATA_DIR / "practice_list.json"


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


def get_all() -> list[dict]:
    return _load()


def add_word(word: str, tip: str, source_phrase: Optional[str] = None, article: Optional[str] = None, entry_type: str = "word") -> dict:
    items = _load()
    # For words, update in place if already exists
    if entry_type == "word":
        for item in items:
            if item.get("type", "word") == "word" and item["word"].lower() == word.lower():
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
        "added_at": datetime.utcnow().isoformat(),
    }
    items.append(entry)
    _save(items)
    return entry


def update_article(word: str, article: str) -> None:
    items = _load()
    for item in items:
        if item.get("type", "word") == "word" and item["word"].lower() == word.lower():
            item["article"] = article
            _save(items)
            return


def remove_word(word: str) -> bool:
    items = _load()
    before = len(items)
    items = [i for i in items if not (i.get("type", "word") == "word" and i["word"].lower() == word.lower())]
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
