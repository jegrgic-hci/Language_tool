import os
import sqlite3
import json
from pathlib import Path
from collections import defaultdict
from typing import Any

from elision import FRENCH_ELISION_RULES, FRENCH_HOMOPHONES, normalize_french

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
                ts          DATETIME DEFAULT (datetime('now')),
                visit_id    TEXT
            )
        """)
        # migrate: add visit_id to existing tables
        try:
            conn.execute("ALTER TABLE events ADD COLUMN visit_id TEXT")
        except Exception:
            pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_code ON events(access_code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_type ON events(event_type)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS coach_cache (
                access_code TEXT PRIMARY KEY,
                payload     TEXT NOT NULL DEFAULT '{}',
                events_at   INTEGER NOT NULL DEFAULT 0,
                ts          DATETIME DEFAULT (datetime('now'))
            )
        """)


def track(session_id: str, access_code: str, event_type: str, payload: dict = None, visit_id: str = None):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO events (session_id, access_code, event_type, payload, visit_id) VALUES (?,?,?,?,?)",
            (session_id, access_code, event_type, json.dumps(payload or {}), visit_id),
        )


# ── Substitution classification ───────────────────────────────────────────────

def _classify_substitution(target_word: str, said: str) -> str:
    """Classify why a word was missed: elision_variant, homophone, acoustic_miss, substitution."""
    if not said:
        return "acoustic_miss"
    tw = target_word.lower().strip()
    sw = said.lower().strip()
    # Homophone: said maps to target via the homophone table (or vice versa)
    if FRENCH_HOMOPHONES.get(sw) == tw or FRENCH_HOMOPHONES.get(tw) == sw:
        return "homophone"
    # Elision variant: normalising the said word produces the target (or vice versa)
    if normalize_french(sw) == normalize_french(tw):
        return "elision_variant"
    if normalize_french(sw).replace("'", "").replace(" ", "") == normalize_french(tw).replace("'", "").replace(" ", ""):
        return "elision_variant"
    return "substitution"


# ── Word accuracy + substitution clusters ─────────────────────────────────────

def get_word_accuracy(access_code: str, min_attempts: int = 5) -> list:
    """Return per-word accuracy stats with substitution clustering.

    word_results rows may be 2-item [word, matched] (legacy) or
    3-item [word, matched, said] (current). Both are handled.
    """
    with _conn() as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE access_code=? AND event_type IN ('phrase_attempted','sentence_drilled','chunk_attempted')",
            (access_code,),
        ).fetchall()
    word_stats: dict = defaultdict(lambda: {"attempts": 0, "misses": 0, "substitutions": defaultdict(int), "types": defaultdict(int)})
    for row in rows:
        p = json.loads(row["payload"])
        for entry in p.get("word_results", []):
            word = entry[0].lower()
            matched = entry[1]
            said = entry[2] if len(entry) > 2 else ""
            word_stats[word]["attempts"] += 1
            if not matched:
                word_stats[word]["misses"] += 1
                if said:
                    word_stats[word]["substitutions"][said.lower()] += 1
                    sub_type = _classify_substitution(word, said)
                    word_stats[word]["types"][sub_type] += 1
                else:
                    word_stats[word]["types"]["acoustic_miss"] += 1
    results = []
    for word, stats in word_stats.items():
        if stats["attempts"] >= min_attempts:
            accuracy = round(1 - stats["misses"] / stats["attempts"], 3)
            top_subs = sorted(stats["substitutions"].items(), key=lambda x: -x[1])[:3]
            dominant_type = max(stats["types"].items(), key=lambda x: x[1])[0] if stats["types"] else None
            results.append({
                "word": word,
                "attempts": stats["attempts"],
                "accuracy": accuracy,
                "top_substitutions": [{"said": s, "count": c} for s, c in top_subs],
                "error_type": dominant_type,
            })
    return sorted(results, key=lambda x: x["accuracy"])


# ── Score trajectories ─────────────────────────────────────────────────────────

def get_score_trajectories(access_code: str) -> dict:
    """Classify each practiced item's learning trajectory.

    Returns counts of mastered / improving / plateaued / stuck items,
    plus the raw worst-performing items for the coach summary.
    """
    with _conn() as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE access_code=? AND event_type IN ('chunk_attempted','sentence_drilled')",
            (access_code,),
        ).fetchall()

    # key → {attempt_number: score}
    item_attempts: dict = defaultdict(dict)
    item_level: dict = {}
    for row in rows:
        p = json.loads(row["payload"])
        key = (p.get("paragraph_id", ""), p.get("chunk_index", 0), p.get("sentence_index", -1))
        attempt = p.get("attempt_number", 1)
        score = p.get("score")
        if score is not None:
            item_attempts[key][attempt] = score
        item_level[key] = str(p.get("level", "?"))

    counts = {"mastered": 0, "improving": 0, "plateaued": 0, "stuck": 0, "single_attempt": 0}
    stuck_items = []

    for key, attempts in item_attempts.items():
        ordered = [attempts[k] for k in sorted(attempts)]
        n = len(ordered)
        first = ordered[0]
        last = ordered[-1]
        best = max(ordered)

        if n == 1:
            counts["single_attempt"] += 1
            if first >= 0.85:
                counts["mastered"] += 1
            continue

        if best >= 0.85:
            counts["mastered"] += 1
            continue

        slope = (last - first) / max(n - 1, 1)
        if slope > 0.05:
            counts["improving"] += 1
        elif n >= 3 and abs(slope) <= 0.05 and last >= 0.6:
            counts["plateaued"] += 1
        elif n >= 3 and last < 0.6:
            counts["stuck"] += 1
            stuck_items.append({"key": str(key), "level": item_level.get(key, "?"), "best": round(best, 2), "last": round(last, 2), "attempts": n})
        else:
            counts["improving"] += 1

    return {
        "counts": counts,
        "stuck_items": sorted(stuck_items, key=lambda x: x["best"])[:10],
    }


# ── Sentence drill breakdown ───────────────────────────────────────────────────

def get_sentence_drill_breakdown(access_code: str) -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE access_code=? AND event_type='sentence_drilled'",
            (access_code,),
        ).fetchall()
    sentence_max_attempt: dict = defaultdict(int)
    sentence_scores: dict = defaultdict(list)
    sentence_level: dict = {}
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
        })
    return results


# ── Coach data ─────────────────────────────────────────────────────────────────

def get_coach_data(access_code: str) -> dict:
    """Structured coaching summary — all deterministic, no LLM."""
    word_acc = get_word_accuracy(access_code, min_attempts=3)
    trajectories = get_score_trajectories(access_code)
    drill_breakdown = get_sentence_drill_breakdown(access_code)

    # acoustic_miss: mic never picks up the word — strictly 0% accuracy, sorted by most attempts
    acoustic     = sorted([w for w in word_acc if w["error_type"] == "acoustic_miss"    and w["accuracy"] == 0.0], key=lambda w: -w["attempts"])
    # pattern errors: wrong more often than not — sorted by attempts × error rate
    homophones   = sorted([w for w in word_acc if w["error_type"] == "homophone"        and w["accuracy"] < 0.5], key=lambda w: -(w["attempts"] * (1 - w["accuracy"])))
    elision      = sorted([w for w in word_acc if w["error_type"] == "elision_variant"  and w["accuracy"] < 0.5], key=lambda w: -(w["attempts"] * (1 - w["accuracy"])))
    substitutions= sorted([w for w in word_acc if w["error_type"] == "substitution"     and w["accuracy"] < 0.5], key=lambda w: -(w["attempts"] * (1 - w["accuracy"])))

    # Overall accuracy band
    avg_acc = _avg([w["accuracy"] for w in word_acc]) if word_acc else None

    # Attempt-count percentile thresholds
    if word_acc:
        attempts_vals = sorted(w["attempts"] for w in word_acc)
        n = len(attempts_vals)
        high_att = attempts_vals[max(0, int(n * 0.75) - 1)]
        mid_att  = attempts_vals[max(0, int(n * 0.50) - 1)]
        low_att  = attempts_vals[max(0, int(n * 0.25) - 1)]
    else:
        high_att = 5
        mid_att  = 4
        low_att  = 3

    # Word coaching buckets — sorted by attempts desc within each bucket
    by_attempts = sorted(word_acc, key=lambda w: -w["attempts"])

    mastered_words   = sorted([w for w in word_acc if w["accuracy"] >= 0.9], key=lambda w: -w["attempts"])
    tech_suspect     = [w for w in word_acc if w["accuracy"] == 0.0 and w["attempts"] >= high_att]
    almost_there     = [w for w in by_attempts if 0.7 <= w["accuracy"] < 0.9 and w["attempts"] >= high_att]
    inconsistent     = [w for w in by_attempts if 0.5 <= w["accuracy"] < 0.7 and w["attempts"] >= high_att]
    quick_pickup     = [w for w in word_acc if w["accuracy"] >= 0.85 and w["attempts"] <= low_att]

    return {
        "overall_avg_accuracy": avg_acc,
        "total_words_tracked": len(word_acc),
        "worst_words": [w for w in word_acc if w["attempts"] >= mid_att],
        "mastered_words": mastered_words,
        "tech_suspect_words": tech_suspect,
        "almost_there_words": almost_there,
        "inconsistent_words": inconsistent,
        "quick_pickup_words": quick_pickup,
        "error_clusters": {
            "acoustic_miss": acoustic,
            "homophone": homophones,
            "elision_variant": elision,
            "substitution": substitutions,
        },
        "trajectories": trajectories,
        "drill_breakdown": drill_breakdown,
    }


# ── Coach cache ────────────────────────────────────────────────────────────────

def _event_count(access_code: str) -> int:
    with _conn() as conn:
        row = conn.execute("SELECT COUNT(*) FROM events WHERE access_code=?", (access_code,)).fetchone()
        return row[0] if row else 0


def get_cached_coach(access_code: str, stale_after: int = 20) -> Any:
    """Return cached coach payload if fresh (within stale_after new events), else None."""
    with _conn() as conn:
        row = conn.execute("SELECT payload, events_at FROM coach_cache WHERE access_code=?", (access_code,)).fetchone()
    if not row:
        return None
    current_count = _event_count(access_code)
    if current_count - row["events_at"] > stale_after:
        return None
    return json.loads(row["payload"])


def set_cached_coach(access_code: str, payload: dict):
    count = _event_count(access_code)
    with _conn() as conn:
        conn.execute(
            "INSERT INTO coach_cache (access_code, payload, events_at) VALUES (?,?,?) ON CONFLICT(access_code) DO UPDATE SET payload=excluded.payload, events_at=excluded.events_at, ts=datetime('now')",
            (access_code, json.dumps(payload), count),
        )


# ── Misc ───────────────────────────────────────────────────────────────────────

def delete_events(access_code: str) -> int:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM events WHERE access_code=?", (access_code,))
        conn.execute("DELETE FROM coach_cache WHERE access_code=?", (access_code,))
        return cur.rowcount


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


def get_session_history(access_code: str, limit: int = 20) -> list:
    """Return per-visit summary rows, most recent first."""
    with _conn() as conn:
        rows = conn.execute(
            """SELECT visit_id, event_type, payload, ts
               FROM events
               WHERE access_code=? AND visit_id IS NOT NULL
               ORDER BY ts ASC""",
            (access_code,),
        ).fetchall()

    # group by visit_id
    visits: dict = defaultdict(lambda: {
        "visit_id": None, "started_at": None, "ended_at": None,
        "duration_seconds": None, "phrase_attempts": 0, "phrase_scores": [],
        "chunk_attempts": 0, "chunk_scores": [], "drill_attempts": 0, "drill_scores": [],
    })
    for row in rows:
        vid = row["visit_id"]
        t   = row["event_type"]
        p   = json.loads(row["payload"])
        ts  = row["ts"]
        v   = visits[vid]
        v["visit_id"] = vid

        if t == "session_start":
            v["started_at"] = ts
        elif t == "session_end":
            v["ended_at"] = ts
            v["duration_seconds"] = p.get("duration_seconds")
        elif t == "phrase_attempted":
            v["phrase_attempts"] += 1
            if p.get("score") is not None:
                v["phrase_scores"].append(p["score"])
        elif t == "chunk_attempted":
            v["chunk_attempts"] += 1
            if p.get("score") is not None:
                v["chunk_scores"].append(p["score"])
        elif t == "sentence_drilled":
            v["drill_attempts"] += 1
            if p.get("score") is not None:
                v["drill_scores"].append(p["score"])

    results = []
    for v in visits.values():
        if not v["started_at"]:
            continue
        results.append({
            "visit_id":        v["visit_id"],
            "started_at":      v["started_at"],
            "ended_at":        v["ended_at"],
            "duration_seconds": v["duration_seconds"],
            "phrase_attempts": v["phrase_attempts"],
            "avg_phrase_score": _avg(v["phrase_scores"]),
            "chunk_attempts":  v["chunk_attempts"],
            "avg_chunk_score": _avg(v["chunk_scores"]),
            "drill_attempts":  v["drill_attempts"],
            "avg_drill_score": _avg(v["drill_scores"]),
        })

    results.sort(key=lambda x: x["started_at"], reverse=True)
    return results[:limit]


def _aggregate(rows) -> dict:
    sessions = set()
    total_shadow_seconds = 0.0

    phrase_scores_by_level: dict = defaultdict(list)

    paragraphs_started: set = set()
    chunk_listen_counts: dict = defaultdict(int)
    chunk_scores_by_level: dict = defaultdict(list)
    chunk_max_attempt: dict = defaultdict(int)
    drill_scores_by_level: dict = defaultdict(list)
    drill_max_attempt: dict = defaultdict(int)

    for row in rows:
        t = row["event_type"]
        p = json.loads(row["payload"])
        vid = row["visit_id"] if "visit_id" in row.keys() else None

        if t == "session_start":
            # prefer visit_id for accurate session count; fall back to session_id
            sessions.add(vid if vid else row["session_id"])

        elif t == "shadowing_time":
            total_shadow_seconds += float(p.get("duration_seconds", 0))

        elif t == "phrase_attempted":
            level = str(p.get("level", "?"))
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
