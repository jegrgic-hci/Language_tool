import os
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data"))
PRACTICE_LIST_FILE = DATA_DIR / "practice_list.json"


def _load() -> list[dict]:
    if not PRACTICE_LIST_FILE.exists():
        return []
    try:
        return json.loads(PRACTICE_LIST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    PRACTICE_LIST_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def get_all() -> list[dict]:
    return _load()


def add_word(word: str, tip: str, source_phrase: Optional[str] = None, article: Optional[str] = None) -> dict:
    items = _load()
    # Update if word already exists
    for item in items:
        if item["word"].lower() == word.lower():
            item["tip"] = tip
            if source_phrase:
                item["source_phrase"] = source_phrase
            if article is not None:
                item["article"] = article
            item["updated_at"] = datetime.utcnow().isoformat()
            _save(items)
            return item
    entry = {
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
        if item["word"].lower() == word.lower():
            item["article"] = article
            _save(items)
            return


def remove_word(word: str) -> bool:
    items = _load()
    before = len(items)
    items = [i for i in items if i["word"].lower() != word.lower()]
    if len(items) < before:
        _save(items)
        return True
    return False
