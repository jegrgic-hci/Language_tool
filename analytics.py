import os
import sqlite3
import json
from pathlib import Path
from collections import defaultdict
from typing import Any

_DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data"))
DB_PATH = _DATA_DIR / "analytics.db"


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT    NOT NULL,
                access_code TEXT    NOT NULL,
                event_type  TEXT    NOT NULL,
                payload     TEXT    NOT NULL DEFAULT '{}',
                ts          DATETIME DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_code ON events(access_code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_type ON events(event_type)")


def track(session_id: str, access_code: str, event_type: str, payload: dict = None):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO events (session_id, access_code, event_type, payload) VALUES (?,?,?,?)",
            (session_id, access_code, event_type, json.dumps(payload or {})),
        )


def get_sentence_drill_breakdown(access_code: str) -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE access_code=? AND event_type='sentence_drilled'",
            (access_code,),
        ).fetchall()
    # per unique sentence: track max attempt_number and all scores
    sentence_max_attempt: dict = defaultdict(int)   # key -> max attempt
    sentence_scores: dict = defaultdict(list)        # key -> [score, ...]
    sentence_level: dict = {}                        # key -> level
    for row in rows:
        p = json.loads(row["payload"])
        key = (p.get("paragraph_id", ""), p.get("chunk_index", 0), p.get("sentence_index", 0))
        attempt = p.get("attempt_number", 1)
        score = p.get("score")
        level = str(p.get("level", "?"))
        sentence_max_attempt[key] = max(sentence_max_attempt[key], attempt)
        if score is not None:
            sentence_scores[key].append(score)
        sentence_level[key] = level
    # aggregate by level
    by_level: dict = defaultdict(lambda: {"sentences": [], "attempts": [], "scores": []})
    for key, max_att in sentence_max_attempt.items():
        lvl = sentence_level[key]
        by_level[lvl]["sentences"].append(key)
        by_level[lvl]["attempts"].append(max_att)
        by_level[lvl]["scores"].extend(sentence_scores.get(key, []))
    results = []
    for lvl in sorted(by_level.keys()):
        d = by_level[lvl]
        avg_att = _avg(d["attempts"])
        avg_score = _avg(d["scores"])
        results.append({
            "level": lvl,
            "sentences_practiced": len(d["sentences"]),
            "avg_attempts": avg_att,
            "avg_score": avg_score,
            "struggling": avg_att is not None and avg_att >= 3,
        })
    return results


def delete_events(access_code: str) -> int:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM events WHERE access_code=?", (access_code,))
        return cur.rowcount


def get_word_accuracy(access_code: str, min_attempts: int = 5) -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE access_code=? AND event_type IN ('phrase_attempted','sentence_drilled')",
            (access_code,),
        ).fetchall()
    word_stats: dict = defaultdict(lambda: {"attempts": 0, "misses": 0})
    for row in rows:
        p = json.loads(row["payload"])
        for entry in p.get("word_results", []):
            word = entry[0].lower()
            matched = entry[1]
            word_stats[word]["attempts"] += 1
            if not matched:
                word_stats[word]["misses"] += 1
    results = []
    for word, stats in word_stats.items():
        if stats["attempts"] >= min_attempts:
            accuracy = round(1 - stats["misses"] / stats["attempts"], 3)
            results.append({"word": word, "attempts": stats["attempts"], "accuracy": accuracy})
    return sorted(results, key=lambda x: x["accuracy"])


def get_analytics() -> dict:
    with _conn() as conn:
        codes = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT access_code FROM events ORDER BY access_code"
            ).fetchall()
        ]
        result = {}
        for code in codes:
            rows = conn.execute(
                "SELECT session_id, event_type, payload FROM events WHERE access_code=?",
                (code,),
            ).fetchall()
            result[code] = _aggregate(rows)
    return result


def _avg(lst: list) -> Any:
    return round(sum(lst) / len(lst), 3) if lst else None


def _aggregate(rows) -> dict:
    sessions = set()
    total_shadow_seconds = 0.0

    phrase_scores_by_level: dict = defaultdict(list)

    paragraphs_started: set = set()
    chunk_listen_counts: dict = defaultdict(int)   # (para_id, chunk_idx) -> count
    chunk_scores_by_level: dict = defaultdict(list)  # level -> [score]
    chunk_max_attempt: dict = defaultdict(int)     # (para_id, chunk_idx) -> max attempt_number
    drill_scores_by_level: dict = defaultdict(list)  # level -> [score]
    drill_max_attempt: dict = defaultdict(int)     # (para_id, chunk_idx, sent_idx) -> max attempt_number

    for row in rows:
        t = row["event_type"]
        p = json.loads(row["payload"])
        sid = row["session_id"]

        if t == "session_start":
            sessions.add(sid)

        elif t == "shadowing_time":
            total_shadow_seconds += float(p.get("duration_seconds", 0))

        elif t == "phrase_attempted":
            level = str(p.get("difficulty", "?"))
            phrase_scores_by_level[level].append(p.get("score", 0))

        elif t == "paragraph_started":
            pid = p.get("paragraph_id", "")
            if pid:
                paragraphs_started.add(pid)

        elif t == "chunk_listened":
            key = (p.get("paragraph_id", ""), p.get("chunk_index", 0))
            chunk_listen_counts[key] += 1

        elif t == "chunk_attempted":
            level = p.get("level", "?")
            chunk_scores_by_level[level].append(p.get("score", 0))
            key = (p.get("paragraph_id", ""), p.get("chunk_index", 0))
            chunk_max_attempt[key] = max(chunk_max_attempt[key], p.get("attempt_number", 1))

        elif t == "sentence_drilled":
            level = p.get("level", "?")
            drill_scores_by_level[level].append(p.get("score", 0))
            key = (p.get("paragraph_id", ""), p.get("chunk_index", 0), p.get("sentence_index", 0))
            drill_max_attempt[key] = max(drill_max_attempt[key], p.get("attempt_number", 1))

    phrase_by_level = {
        lvl: {"attempts": len(scores), "avg_score": _avg(scores)}
        for lvl, scores in sorted(phrase_scores_by_level.items())
    }

    all_para_levels = set(list(chunk_scores_by_level.keys()) + list(drill_scores_by_level.keys()))
    para_by_level = {}
    for lvl in sorted(all_para_levels):
        ca = chunk_scores_by_level.get(lvl, [])
        sd = drill_scores_by_level.get(lvl, [])
        para_by_level[lvl] = {
            "chunk_attempts": len(ca),
            "avg_chunk_score": _avg(ca),
            "sentence_drills": len(sd),
            "avg_drill_score": _avg(sd),
        }

    return {
        "sessions": len(sessions),
        "total_shadowing_minutes": round(total_shadow_seconds / 60, 1),
        "phrase_shadowing": {
            "total_attempts": sum(len(v) for v in phrase_scores_by_level.values()),
            "by_level": phrase_by_level,
        },
        "paragraph_shadowing": {
            "paragraphs_started": len(paragraphs_started),
            "avg_listens_per_chunk": _avg(list(chunk_listen_counts.values())),
            "avg_attempts_per_chunk": _avg(list(chunk_max_attempt.values())),
            "avg_drills_per_sentence": _avg(list(drill_max_attempt.values())),
            "by_level": para_by_level,
        },
    }
