import os
import sqlite3
import json
import secrets
import string
from datetime import date, datetime, timedelta
from pathlib import Path
from collections import defaultdict, Counter
from typing import Any, Optional

from elision import FRENCH_ELISION_RULES, FRENCH_HOMOPHONES, normalize_french

_DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data"))
DB_PATH = _DATA_DIR / "analytics.db"


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


SESSION_GAP_MINUTES = 20

# Events that count as active practice for gap-based session splitting.
# session_end is excluded — it fires on tab close, long after actual practice ends.
_SESSION_EVENTS = frozenset({
    'session_start', 'paragraph_started', 'chunk_listened',
    'phrase_attempted', 'paragraph_attempted', 'paragraph_drilled', 'word_attempted',
})


def _split_session_groups(ts_list: list) -> list:
    """Split a sorted list of timestamp strings into (start_ts, end_ts) session pairs.

    A new session begins whenever the gap between consecutive events exceeds
    SESSION_GAP_MINUTES. Returns one (start, end) tuple per session.
    """
    if not ts_list:
        return []
    sessions = []
    start = ts_list[0]
    prev  = ts_list[0]
    for ts in ts_list[1:]:
        try:
            gap = (datetime.fromisoformat(ts.replace(' ', 'T')) -
                   datetime.fromisoformat(prev.replace(' ', 'T'))).total_seconds() / 60
        except Exception:
            gap = 0
        if gap > SESSION_GAP_MINUTES:
            sessions.append((start, prev))
            start = ts
        prev = ts
    sessions.append((start, prev))
    return sessions


def _group_events_into_sessions(rows: list) -> list:
    """Group (ts, event_type, payload_dict) rows into session buckets using the
    SESSION_GAP_MINUTES idle threshold. Returns a list of lists."""
    if not rows:
        return []
    sessions = []
    current = [rows[0]]
    for row in rows[1:]:
        try:
            gap = (datetime.fromisoformat(row[0].replace(' ', 'T')) -
                   datetime.fromisoformat(current[-1][0].replace(' ', 'T'))).total_seconds() / 60
        except Exception:
            gap = 0
        if gap > SESSION_GAP_MINUTES:
            sessions.append(current)
            current = [row]
        else:
            current.append(row)
    sessions.append(current)
    return sessions


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
        # migrate: rename legacy event types to canonical taxonomy
        conn.execute("UPDATE events SET event_type='paragraph_attempted' WHERE event_type='chunk_attempted'")
        conn.execute("UPDATE events SET event_type='paragraph_drilled' WHERE event_type='sentence_drilled'")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS coach_cache (
                access_code TEXT PRIMARY KEY,
                payload     TEXT NOT NULL DEFAULT '{}',
                events_at   INTEGER NOT NULL DEFAULT 0,
                ts          DATETIME DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS teachers (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                name  TEXT NOT NULL,
                email TEXT NOT NULL DEFAULT '',
                key   TEXT NOT NULL UNIQUE,
                ts    DATETIME DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS students (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                access_code  TEXT NOT NULL UNIQUE,
                teacher_id   INTEGER REFERENCES teachers(id),
                name         TEXT NOT NULL DEFAULT '',
                email        TEXT NOT NULL DEFAULT '',
                lesson_days  TEXT NOT NULL DEFAULT '[]',
                lesson_time  TEXT NOT NULL DEFAULT '',
                notes        TEXT NOT NULL DEFAULT '',
                ts           DATETIME DEFAULT (datetime('now'))
            )
        """)


def track(session_id: str, access_code: str, event_type: str, payload: dict = None, visit_id: str = None):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO events (session_id, access_code, event_type, payload, visit_id) VALUES (?,?,?,?,?)",
            (session_id, access_code, event_type, json.dumps(payload or {}), visit_id),
        )


# ── Teachers & students ────────────────────────────────────────────────────────

def create_teacher(name: str, email: str, key: str) -> dict:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO teachers (name, email, key) VALUES (?,?,?)",
            (name, email, key),
        )
        return {"id": cur.lastrowid, "name": name, "email": email, "key": key}


def get_teacher_by_key(key: str) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM teachers WHERE key=?", (key,)).fetchone()
    return dict(row) if row else None


def get_all_teachers() -> list:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM teachers ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def _generate_access_code() -> str:
    alphabet = string.ascii_lowercase + string.digits
    with _conn() as conn:
        for _ in range(30):
            code = "".join(secrets.choice(alphabet) for _ in range(6))
            if not conn.execute("SELECT 1 FROM students WHERE access_code=?", (code,)).fetchone():
                return code
    raise RuntimeError("Could not generate unique access code")


def add_student(name: str, email: str, lesson_days: list,
                lesson_time: str, notes: str, teacher_id: Optional[int] = None) -> dict:
    code = _generate_access_code()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO students (access_code, teacher_id, name, email, lesson_days, lesson_time, notes) VALUES (?,?,?,?,?,?,?)",
            (code, teacher_id, name, email, json.dumps(lesson_days), lesson_time, notes),
        )
    return {"access_code": code, "name": name, "email": email,
            "lesson_days": lesson_days, "lesson_time": lesson_time, "notes": notes}


def update_student(access_code: str, name: Optional[str] = None, email: Optional[str] = None,
                   lesson_days: Optional[list] = None, lesson_time: Optional[str] = None,
                   notes: Optional[str] = None) -> bool:
    fields, vals = [], []
    if name is not None:       fields.append("name=?");         vals.append(name)
    if email is not None:      fields.append("email=?");        vals.append(email)
    if lesson_days is not None: fields.append("lesson_days=?"); vals.append(json.dumps(lesson_days))
    if lesson_time is not None: fields.append("lesson_time=?"); vals.append(lesson_time)
    if notes is not None:      fields.append("notes=?");        vals.append(notes)
    if not fields:
        return False
    vals.append(access_code)
    with _conn() as conn:
        cur = conn.execute(f"UPDATE students SET {', '.join(fields)} WHERE access_code=?", vals)
    return cur.rowcount > 0


def get_students_for_teacher(teacher_id: int) -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM students WHERE teacher_id=? ORDER BY name",
            (teacher_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_students() -> list:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM students ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_student_by_code(access_code: str) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM students WHERE access_code=?", (access_code,)).fetchone()
    return dict(row) if row else None


def last_lesson_date(lesson_days_json: str) -> Optional[date]:
    """Return the most recent completed lesson date (never today — today's lesson may not have happened yet)."""
    try:
        days = json.loads(lesson_days_json) if lesson_days_json else []
    except Exception:
        return None
    if not days:
        return None
    day_map = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
    lesson_weekdays = {day_map[d] for d in days if d in day_map}
    if not lesson_weekdays:
        return None
    today = date.today()
    for i in range(1, 9):  # start at 1 — skip today regardless of whether it's a lesson day
        d = today - timedelta(days=i)
        if d.weekday() in lesson_weekdays:
            return d
    return None


def next_lesson_date(lesson_days_json: str) -> Optional[date]:
    """Return the next upcoming lesson date (includes today if today is a lesson day)."""
    try:
        days = json.loads(lesson_days_json) if lesson_days_json else []
    except Exception:
        return None
    if not days:
        return None
    day_map = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
    lesson_weekdays = {day_map[d] for d in days if d in day_map}
    if not lesson_weekdays:
        return None
    today = date.today()
    for i in range(0, 8):
        d = today + timedelta(days=i)
        if d.weekday() in lesson_weekdays:
            return d
    return None


def get_roster() -> list:
    """All students with key stats for the roster card view, sorted by next lesson date."""
    students = get_all_students()
    today = date.today()
    since_7d  = (today - timedelta(days=6)).isoformat()
    since_30d = (today - timedelta(days=29)).isoformat()

    with _conn() as conn:
        # Last practice event per student (excludes pure session/listen events)
        last_rows = conn.execute(
            "SELECT access_code, MAX(date(ts)) AS last_date FROM events "
            "WHERE event_type NOT IN ('session_start','session_end','shadowing_time') "
            "GROUP BY access_code"
        ).fetchall()
        last_practice = {r["access_code"]: r["last_date"] for r in last_rows}

        # Session counts + durations via gap analysis (replaces visit_id counting)
        sess_ts_rows = conn.execute(
            "SELECT access_code, ts FROM events "
            "WHERE date(ts) >= ? AND ts IS NOT NULL "
            "AND event_type IN ('session_start','paragraph_started','chunk_listened',"
            "'phrase_attempted','paragraph_attempted','paragraph_drilled','word_attempted') "
            "ORDER BY access_code, ts ASC",
            (since_30d,),
        ).fetchall()
        ts_by_code: dict = defaultdict(list)
        for r in sess_ts_rows:
            ts_by_code[r["access_code"]].append(r["ts"])

        sessions_7d: dict = {}
        sessions_30d_map: dict = {}
        duration_7d: dict = {}
        for code, timestamps in ts_by_code.items():
            groups = _split_session_groups(timestamps)
            sessions_30d_map[code] = len(groups)
            sev_7d = [g for g in groups if g[0] >= since_7d]
            sessions_7d[code] = len(sev_7d)
            total_secs = 0
            for start, end in sev_7d:
                try:
                    total_secs += int((
                        datetime.fromisoformat(end.replace(' ', 'T')) -
                        datetime.fromisoformat(start.replace(' ', 'T'))
                    ).total_seconds())
                except Exception:
                    pass
            duration_7d[code] = total_secs

        # 7d: practice event count per day (for mini bar chart)
        daily_rows = conn.execute(
            "SELECT access_code, date(ts) AS day, COUNT(*) AS cnt "
            "FROM events "
            "WHERE event_type IN "
            "  ('phrase_attempted','paragraph_attempted','paragraph_drilled','word_attempted') "
            "AND date(ts) >= ? "
            "GROUP BY access_code, day",
            (since_7d,),
        ).fetchall()
        daily_activity: dict = defaultdict(dict)
        for r in daily_rows:
            daily_activity[r["access_code"]][r["day"]] = r["cnt"]

        # Recent topics (30d, top 4 by count)
        topic_rows = conn.execute(
            "SELECT access_code, json_extract(payload,'$.topic') AS topic, COUNT(*) AS cnt "
            "FROM events "
            "WHERE event_type IN ('phrase_attempted','paragraph_attempted','paragraph_started') "
            "AND date(ts) >= ? AND json_extract(payload,'$.topic') IS NOT NULL "
            "GROUP BY access_code, topic ORDER BY cnt DESC",
            (since_30d,),
        ).fetchall()
        topics_map: dict = defaultdict(list)
        for r in topic_rows:
            if r["topic"] and len(topics_map[r["access_code"]]) < 4:
                topics_map[r["access_code"]].append(r["topic"])

        # Scoring events with timestamps — used for all-time, 7d, and since-lesson accuracy
        acc_rows = conn.execute(
            "SELECT access_code, date(ts) AS day, "
            "CAST(json_extract(payload,'$.score') AS REAL) AS score "
            "FROM events "
            "WHERE event_type IN ('phrase_attempted','paragraph_attempted') "
            "AND json_extract(payload,'$.score') IS NOT NULL",
        ).fetchall()
        acc_events: dict = defaultdict(list)
        for r in acc_rows:
            acc_events[r["access_code"]].append((r["day"], float(r["score"])))

        def _avg(scores: list) -> Optional[float]:
            return round(sum(scores) / len(scores), 3) if scores else None

        accuracy: dict = {}
        for code, pairs in acc_events.items():
            all_scores = [s for _, s in pairs]
            scores_7d  = [s for d, s in pairs if d >= since_7d]
            accuracy[code] = {
                "score":        _avg(all_scores),
                "attempts":     len(all_scores),
                "avg_score_7d": _avg(scores_7d),
            }

    # Day buckets: [6 days ago … today]
    day_keys = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]

    result = []
    for s in students:
        code             = s["access_code"]
        lesson_days_json = s.get("lesson_days") or "[]"
        last             = last_practice.get(code)
        acc              = accuracy.get(code, {})

        last_lesson  = last_lesson_date(lesson_days_json)
        next_lesson  = next_lesson_date(lesson_days_json)

        days_since = (today - date.fromisoformat(last)).days if last else None
        days_until = (next_lesson - today).days if next_lesson else None

        # Health signal
        if last is None:
            health = "grey"
        elif days_since > 14:
            health = "red"
        elif days_since > 7:
            health = "amber"
        elif acc.get("score", 1.0) < 0.45 and acc.get("attempts", 0) >= 10:
            health = "amber"
        else:
            health = "green"

        # Accuracy since last lesson (per-student date filter)
        last_lesson_str = last_lesson.isoformat() if last_lesson else None
        scores_since_lesson = [
            s for d, s in acc_events.get(code, [])
            if last_lesson_str and d > last_lesson_str
        ]
        avg_score_since_lesson = _avg(scores_since_lesson)

        lesson_days_list: list = []
        try:
            lesson_days_list = json.loads(lesson_days_json)
        except Exception:
            pass

        result.append({
            "access_code":             code,
            "name":                    s.get("name"),
            "lesson_days":             lesson_days_list,
            "lesson_time":             s.get("lesson_time"),
            "last_practice":           last,
            "days_since_practice":     days_since,
            "last_lesson":             last_lesson_str,
            "next_lesson":             next_lesson.isoformat() if next_lesson else None,
            "days_until_next":         days_until,
            "sessions_7d":             sessions_7d.get(code, 0),
            "practice_minutes_7d":     round(duration_7d.get(code, 0) / 60),
            "activity_7d":             [daily_activity.get(code, {}).get(d, 0) for d in day_keys],
            "sessions_30d":            sessions_30d_map.get(code, 0),
            "topics":                  topics_map.get(code, []),
            "avg_score":               acc.get("score"),
            "avg_score_7d":            acc.get("avg_score_7d"),
            "avg_score_since_lesson":  avg_score_since_lesson,
            "health":                  health,
        })

    result.sort(key=lambda x: (x["days_until_next"] is None, x["days_until_next"] or 0))
    return result


def get_practice_since(access_code: str, since: date) -> dict:
    """Aggregate practice events strictly after `since` date.

    Sessions are counted using activity-gap analysis (SESSION_GAP_MINUTES idle threshold)
    rather than visit_id, so a student returning to the same tab hours later counts as
    a new session.
    """
    since_str = since.isoformat()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT event_type, payload, ts FROM events "
            "WHERE access_code=? AND date(ts) > ? AND ts IS NOT NULL "
            "AND event_type IN ('session_start','paragraph_started','chunk_listened',"
            "'phrase_attempted','paragraph_attempted','paragraph_drilled','word_attempted') "
            "ORDER BY ts ASC",
            (access_code, since_str),
        ).fetchall()

    events = [(r["ts"], r["event_type"], json.loads(r["payload"])) for r in rows]
    sess_groups = _group_events_into_sessions(events)

    days_active: set = set()
    total_attempts = 0
    scores: list = []
    word_stats: dict = defaultdict(lambda: {"attempts": 0, "misses": 0})
    total_seconds = 0
    session_durations: list = []

    for group in sess_groups:
        start_ts, end_ts = group[0][0], group[-1][0]
        try:
            dur = int((datetime.fromisoformat(end_ts.replace(' ', 'T')) -
                       datetime.fromisoformat(start_ts.replace(' ', 'T'))).total_seconds())
            session_durations.append(dur)
            total_seconds += dur
        except Exception:
            pass

        for ts, t, p in group:
            days_active.add(ts[:10])
            if t in ("phrase_attempted", "paragraph_attempted", "paragraph_drilled", "word_attempted"):
                total_attempts += 1
                score = p.get("score")
                if score is not None:
                    scores.append(score)
                for entry in p.get("word_results", []):
                    word = entry[0].lower()
                    matched = entry[1]
                    word_stats[word]["attempts"] += 1
                    if not matched:
                        word_stats[word]["misses"] += 1

    struggles = []
    for word, s in word_stats.items():
        if s["attempts"] >= 2:
            acc = round(1 - s["misses"] / s["attempts"], 2)
            if acc < 0.6:
                struggles.append({"word": word, "accuracy": acc})
    struggles.sort(key=lambda x: x["accuracy"])

    return {
        "sessions":       len(sess_groups),
        "days_active":    len(days_active),
        "total_attempts": total_attempts,
        "avg_score":      _avg(scores),
        "struggles":      struggles[:8],
        "total_seconds":  total_seconds if session_durations else None,
        "avg_seconds":    round(total_seconds / len(session_durations)) if session_durations else None,
    }


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
            "SELECT payload FROM events WHERE access_code=? AND event_type IN ('phrase_attempted','paragraph_attempted','paragraph_drilled','word_attempted')",
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
            "SELECT payload FROM events WHERE access_code=? AND event_type IN ('paragraph_attempted','paragraph_drilled')",
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
            "SELECT payload FROM events WHERE access_code=? AND event_type='paragraph_drilled'",
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

def get_first_event_ts(access_code: str) -> Optional[str]:
    with _conn() as conn:
        row = conn.execute("SELECT MIN(ts) FROM events WHERE access_code=?", (access_code,)).fetchone()
    return row[0] if row else None


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
    """Return per-session summary rows, most recent first.

    Sessions are computed via activity-gap analysis: a new session begins whenever
    the gap between consecutive practice events exceeds SESSION_GAP_MINUTES.
    """
    _PASS_PARA   = 0.70
    _PASS_PHRASE = 0.90

    with _conn() as conn:
        rows = conn.execute(
            "SELECT event_type, payload, ts FROM events "
            "WHERE access_code=? AND ts IS NOT NULL "
            "AND event_type IN ('session_start','paragraph_started','chunk_listened',"
            "'phrase_attempted','paragraph_attempted','paragraph_drilled','word_attempted') "
            "ORDER BY ts ASC",
            (access_code,),
        ).fetchall()

    events = [(r["ts"], r["event_type"], json.loads(r["payload"])) for r in rows]
    sess_groups = _group_events_into_sessions(events)

    seen_words: set = set()
    results = []

    for group in sess_groups:
        started_at = group[0][0]
        ended_at   = group[-1][0]
        try:
            duration_seconds = int((
                datetime.fromisoformat(ended_at.replace(' ', 'T')) -
                datetime.fromisoformat(started_at.replace(' ', 'T'))
            ).total_seconds())
        except Exception:
            duration_seconds = None

        phrase_attempts = 0; phrase_scores = []
        para_attempts   = 0; para_scores   = []
        para_drills     = 0; para_drill_scores = []
        word_attempts   = 0
        word_set: set   = set()

        for ts, t, p in group:
            if t == "phrase_attempted":
                phrase_attempts += 1
                if p.get("score") is not None:
                    phrase_scores.append(p["score"])
                for wr in p.get("word_results", []):
                    if wr and wr[0]:
                        word_set.add(wr[0].lower())
            elif t == "paragraph_attempted":
                para_attempts += 1
                if p.get("score") is not None:
                    para_scores.append(p["score"])
                for wr in p.get("word_results", []):
                    if wr and wr[0]:
                        word_set.add(wr[0].lower())
            elif t == "paragraph_drilled":
                para_drills += 1
                if p.get("score") is not None:
                    para_drill_scores.append(p["score"])
                for wr in p.get("word_results", []):
                    if wr and wr[0]:
                        word_set.add(wr[0].lower())
            elif t == "word_attempted":
                word_attempts += 1

        results.append({
            "started_at":                started_at,
            "ended_at":                  ended_at,
            "duration_seconds":          duration_seconds,
            "word_attempts":             word_attempts,
            "words_new":                 len(word_set - seen_words),
            "words_revisited":           len(word_set & seen_words),
            "phrase_attempts":           phrase_attempts,
            "phrase_passed":             sum(1 for s in phrase_scores if s >= _PASS_PHRASE),
            "avg_phrase_score":          _avg(phrase_scores),
            "paragraph_attempts":        para_attempts,
            "paragraph_passed":          sum(1 for s in para_scores if s >= _PASS_PARA),
            "avg_paragraph_score":       _avg(para_scores),
            "paragraph_drills":          para_drills,
            "avg_paragraph_drill_score": _avg(para_drill_scores),
        })
        seen_words |= word_set

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

        elif t == "paragraph_attempted":
            level = p.get("level", "?")
            chunk_scores_by_level[level].append(p.get("score", 0))
            key = (p.get("paragraph_id", ""), p.get("chunk_index", 0))
            chunk_max_attempt[key] = max(chunk_max_attempt[key], p.get("attempt_number", 1))

        elif t == "paragraph_drilled":
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


# ── Paragraph exercise stats ───────────────────────────────────────────────────

def get_paragraph_exercise_stats(access_code: str, since_days: Optional[int] = None) -> dict:
    _PASS = 0.70
    ts_filter = f" AND ts >= datetime('now', '-{since_days} days')" if since_days else ""
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT event_type, payload FROM events WHERE access_code=? AND event_type IN ('paragraph_started','paragraph_attempted','paragraph_drilled','word_attempted'){ts_filter}",
            (access_code,),
        ).fetchall()

    # paragraph_id → level (from paragraph_started events in this window)
    para_level: dict = {}
    paragraphs_started: set = set()
    paragraphs_completed: set = set()
    phrases_practiced = 0
    drill_scores: list = []
    words_practiced = 0

    # per-level buckets
    lvl_started: dict     = defaultdict(set)
    lvl_completed: dict   = defaultdict(set)
    lvl_drills: dict      = defaultdict(int)
    lvl_drill_scores: dict = defaultdict(list)

    for row in rows:
        t = row["event_type"]
        p = json.loads(row["payload"])
        if t == "paragraph_started":
            pid   = p.get("paragraph_id", "")
            level = str(p.get("level", "?"))
            if pid:
                paragraphs_started.add(pid)
                para_level[pid] = level
                lvl_started[level].add(pid)
        elif t == "paragraph_attempted":
            score = p.get("score")
            pid   = p.get("paragraph_id", "")
            level = str(p.get("level") or para_level.get(pid, "?"))
            if pid and score is not None and score >= _PASS:
                paragraphs_completed.add(pid)
                lvl_completed[level].add(pid)
        elif t == "paragraph_drilled":
            level = str(p.get("level", "?"))
            phrases_practiced += 1
            lvl_drills[level] += 1
            if p.get("score") is not None:
                drill_scores.append(p["score"])
                lvl_drill_scores[level].append(p["score"])
        elif t == "word_attempted":
            if p.get("source") == "paragraph_drill":
                words_practiced += 1

    all_levels = sorted(set(list(lvl_started.keys()) + list(lvl_drills.keys())))
    by_level = {}
    for lvl in all_levels:
        ns = len(lvl_started.get(lvl, set()))
        nc = len(lvl_completed.get(lvl, set()))
        ds = lvl_drill_scores.get(lvl, [])
        by_level[lvl] = {
            "paragraphs_started":   ns,
            "paragraphs_completed": nc,
            "completion_rate":      round(nc / ns, 2) if ns else None,
            "phrases_drilled":      lvl_drills.get(lvl, 0),
            "avg_drill_score":      _avg(ds),
        }

    n_started   = len(paragraphs_started)
    n_completed = len(paragraphs_completed)
    return {
        "paragraphs_started":   n_started,
        "phrases_practiced":    phrases_practiced,
        "avg_drill_score":      _avg(drill_scores),
        "words_practiced":      words_practiced,
        "paragraphs_completed": n_completed,
        "completion_rate":      round(n_completed / n_started, 2) if n_started else None,
        "by_level":             by_level,
    }


# ── Phrase exercise stats ──────────────────────────────────────────────────────

def get_phrase_exercise_stats(access_code: str, since_days: Optional[int] = None) -> dict:
    _PASS = 0.90
    ts_filter = f" AND ts >= datetime('now', '-{since_days} days')" if since_days else ""
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT event_type, payload FROM events WHERE access_code=? AND event_type IN ('phrase_attempted','word_attempted'){ts_filter}",
            (access_code,),
        ).fetchall()

    phrases_started = 0
    phrases_completed = 0
    completion_attempts: list = []
    phrases_stuck = 0
    words_practiced = 0

    lvl_started: dict     = defaultdict(int)
    lvl_completed: dict   = defaultdict(int)
    lvl_stuck: dict       = defaultdict(int)
    lvl_comp_att: dict    = defaultdict(list)

    for row in rows:
        t = row["event_type"]
        p = json.loads(row["payload"])
        if t == "phrase_attempted":
            level  = str(p.get("level", "?"))
            phrases_started += 1
            lvl_started[level] += 1
            passed = p.get("passed") or (p.get("score", 0) >= _PASS)
            att = p.get("attempt_number", 1)
            if passed:
                phrases_completed += 1
                lvl_completed[level] += 1
                if att is not None:
                    completion_attempts.append(att)
                    lvl_comp_att[level].append(att)
            elif att >= 3:
                phrases_stuck += 1
                lvl_stuck[level] += 1
        elif t == "word_attempted":
            if p.get("source") == "phrase_exercise":
                words_practiced += 1

    all_levels = sorted(set(list(lvl_started.keys())))
    by_level = {}
    for lvl in all_levels:
        ns  = lvl_started.get(lvl, 0)
        nc  = lvl_completed.get(lvl, 0)
        nst = lvl_stuck.get(lvl, 0)
        ca  = lvl_comp_att.get(lvl, [])
        by_level[lvl] = {
            "phrases_started":          ns,
            "phrases_completed":        nc,
            "completion_rate":          round(nc / ns, 2) if ns else None,
            "phrases_stuck":            nst,
            "avg_attempts_to_complete": round(_avg(ca), 1) if ca else None,
        }

    return {
        "phrases_started":          phrases_started,
        "words_practiced":          words_practiced,
        "phrases_completed":        phrases_completed,
        "phrases_stuck":            phrases_stuck,
        "avg_attempts_to_complete": round(_avg(completion_attempts), 1) if completion_attempts else None,
        "by_level":                 by_level,
    }


# ── Topic / content coverage ───────────────────────────────────────────────────

def get_topic_coverage(access_code: str) -> list:
    """Aggregate scored practice by topic across phrase + paragraph exercises.

    `topic` is carried directly on phrase_attempted, and on paragraph_started
    (keyed by paragraph_id) — paragraph_attempted/drilled events are joined back
    to their topic via that map.

    Returns [{topic, attempts, avg_score, last_practiced}] sorted by attempts desc.
    """
    with _conn() as conn:
        rows = conn.execute(
            "SELECT event_type, payload, ts FROM events WHERE access_code=? "
            "AND event_type IN ('paragraph_started','phrase_attempted','paragraph_attempted','paragraph_drilled')",
            (access_code,),
        ).fetchall()

    # paragraph_id → topic
    para_topic: dict = {}
    for row in rows:
        if row["event_type"] == "paragraph_started":
            p = json.loads(row["payload"])
            pid, topic = p.get("paragraph_id"), p.get("topic")
            if pid and topic:
                para_topic[pid] = topic

    topic_stats: dict = defaultdict(lambda: {"attempts": 0, "scores": [], "last": ""})
    for row in rows:
        t = row["event_type"]
        if t == "paragraph_started":
            continue  # only used to build the topic map above
        p = json.loads(row["payload"])
        if t == "phrase_attempted":
            topic = p.get("topic")
        else:  # paragraph_attempted / paragraph_drilled
            topic = para_topic.get(p.get("paragraph_id"))
        if not topic:
            continue
        s = topic_stats[topic]
        s["attempts"] += 1
        score = p.get("score")
        if score is not None:
            s["scores"].append(score)
        ts = row["ts"] or ""
        if ts > s["last"]:
            s["last"] = ts

    results = [
        {
            "topic": topic,
            "attempts": s["attempts"],
            "avg_score": _avg(s["scores"]),
            "last_practiced": s["last"] or None,
        }
        for topic, s in topic_stats.items()
    ]
    return sorted(results, key=lambda x: -x["attempts"])


# ── Listen-to-speak ratio ──────────────────────────────────────────────────────

def get_listen_speak_ratio(access_code: str) -> dict:
    """How much a student replays audio relative to speaking attempts.

    Listening happens on paragraph chunks (chunk_listened); speaking attempts are
    paragraph_attempted + paragraph_drilled. A high ratio means a lot of replaying
    before attempting — often a struggle signal.
    """
    with _conn() as conn:
        rows = conn.execute(
            "SELECT event_type, payload FROM events WHERE access_code=? "
            "AND event_type IN ('chunk_listened','paragraph_attempted','paragraph_drilled')",
            (access_code,),
        ).fetchall()

    listens = 0
    attempts = 0
    chunk_listens: dict = defaultdict(int)
    for row in rows:
        t = row["event_type"]
        if t == "chunk_listened":
            listens += 1
            p = json.loads(row["payload"])
            chunk_listens[(p.get("paragraph_id", ""), p.get("chunk_index", 0))] += 1
        else:
            attempts += 1

    return {
        "listens": listens,
        "speak_attempts": attempts,
        "ratio": round(listens / attempts, 2) if attempts else None,
        "avg_replays_per_chunk": _avg(list(chunk_listens.values())),
    }


# ── Score trend (progress over time) ───────────────────────────────────────────

def get_score_trend(access_code: str, weeks: int = 8) -> dict:
    """Weekly practice-score time series + recent-vs-lifetime comparison.

    Scored attempts (phrase / paragraph / drill / word) are bucketed by ISO week
    (Monday start). The last `weeks` calendar weeks are returned — including empty
    ones — so the chart x-axis stays continuous. `delta` is the last-30-day average
    minus the prior 30 days (the "is the student improving" signal).
    """
    with _conn() as conn:
        rows = conn.execute(
            "SELECT payload, ts FROM events WHERE access_code=? "
            "AND event_type IN ('phrase_attempted','paragraph_attempted','paragraph_drilled','word_attempted')",
            (access_code,),
        ).fetchall()

    today = date.today()
    cur_week_start = today - timedelta(days=today.weekday())
    week_starts = [cur_week_start - timedelta(weeks=(weeks - 1 - i)) for i in range(weeks)]
    week_index = {ws: i for i, ws in enumerate(week_starts)}
    buckets = [{"week_start": ws.isoformat(), "attempts": 0, "scores": [], "days": set()}
               for ws in week_starts]

    cutoff_30 = today - timedelta(days=30)
    cutoff_60 = today - timedelta(days=60)
    recent_scores: list = []
    prior_scores: list = []
    lifetime_scores: list = []

    lvl_recent: dict   = defaultdict(list)
    lvl_lifetime: dict = defaultdict(list)

    for row in rows:
        p = json.loads(row["payload"])
        score = p.get("score")
        ts = row["ts"] or ""
        if score is None or not ts:
            continue
        try:
            d = date.fromisoformat(ts[:10])
        except Exception:
            continue
        level = str(p.get("level", "?"))
        lifetime_scores.append(score)
        lvl_lifetime[level].append(score)
        if d >= cutoff_30:
            recent_scores.append(score)
            lvl_recent[level].append(score)
        elif d >= cutoff_60:
            prior_scores.append(score)
        ws = d - timedelta(days=d.weekday())
        idx = week_index.get(ws)
        if idx is not None:
            b = buckets[idx]
            b["attempts"] += 1
            b["scores"].append(score)
            b["days"].add(ts[:10])

    weekly = [{
        "week_start":  b["week_start"],
        "attempts":    b["attempts"],
        "avg_score":   _avg(b["scores"]),
        "active_days": len(b["days"]),
    } for b in buckets]

    recent_avg   = _avg(recent_scores)
    prior_avg    = _avg(prior_scores)
    lifetime_avg = _avg(lifetime_scores)
    delta = (round(recent_avg - prior_avg, 3)
             if recent_avg is not None and prior_avg is not None else None)

    all_levels = sorted(set(list(lvl_recent.keys()) + list(lvl_lifetime.keys())))
    by_level = {
        lvl: {
            "recent_avg":    _avg(lvl_recent.get(lvl, [])),
            "lifetime_avg":  _avg(lvl_lifetime.get(lvl, [])),
            "recent_attempts":   len(lvl_recent.get(lvl, [])),
            "lifetime_attempts": len(lvl_lifetime.get(lvl, [])),
        }
        for lvl in all_levels
    }

    return {
        "weekly":       weekly,
        "recent_avg":   recent_avg,
        "lifetime_avg": lifetime_avg,
        "delta":        delta,
        "by_level":     by_level,
    }


# ── Student progress trend (landing-page chart) ─────────────────────────────────

# Maps the raw event_type to the exercise "type" used by the progress chart.
_PROGRESS_EVENT_TYPE = {
    "phrase_attempted":    "phrase",
    "paragraph_attempted": "paragraph",
    "paragraph_drilled":   "paragraph",
    "word_attempted":      "word",
}

# Pass thresholds per exercise type (mirrors analytics.md: paragraph 0.70, phrase 0.90).
_PROGRESS_PASS = {"paragraph": 0.70, "phrase": 0.90, "word": 0.90}

_LEVEL_RANK = {"?": 0, "A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6}


def get_progress_trend(access_code: str, weeks: int = 8) -> dict:
    """Weekly score/pass time series, split by exercise type AND CEFR level.

    Powers the student landing-page progress chart (DV3 level-split). For each
    exercise type (``overall`` / ``paragraph`` / ``phrase`` / ``word``) and each
    CEFR level, a per-week array of avg score, pass rate and attempt count is
    returned, with ``None`` in weeks that had no practice — so a line spans only
    its active weeks. Week bucketing mirrors :func:`get_score_trend`.
    """
    with _conn() as conn:
        rows = conn.execute(
            "SELECT event_type, payload, ts FROM events WHERE access_code=? "
            "AND event_type IN ('phrase_attempted','paragraph_attempted','paragraph_drilled','word_attempted')",
            (access_code,),
        ).fetchall()

    today = date.today()
    cur_week_start = today - timedelta(days=today.weekday())
    week_starts = [cur_week_start - timedelta(weeks=(weeks - 1 - i)) for i in range(weeks)]
    week_index = {ws: i for i, ws in enumerate(week_starts)}

    def _new_weeks():
        return [{"scores": [], "passes": 0, "attempts": 0} for _ in range(weeks)]

    # acc[type][level] -> per-week buckets
    acc: dict = defaultdict(lambda: defaultdict(_new_weeks))

    cutoff_30 = today - timedelta(days=30)
    cutoff_60 = today - timedelta(days=60)
    recent_scores: list = []
    prior_scores: list = []
    lifetime_scores: list = []

    for row in rows:
        p = json.loads(row["payload"])
        score = p.get("score")
        ts = row["ts"] or ""
        if score is None or not ts:
            continue
        try:
            d = date.fromisoformat(ts[:10])
        except Exception:
            continue
        ws = d - timedelta(days=d.weekday())
        idx = week_index.get(ws)

        etype = _PROGRESS_EVENT_TYPE.get(row["event_type"], "word")
        level = str(p.get("level") or "?") or "?"
        passed = score >= _PROGRESS_PASS.get(etype, 0.90)

        if idx is not None:
            for tkey in ("overall", etype):
                wk = acc[tkey][level][idx]
                wk["scores"].append(score)
                wk["attempts"] += 1
                if passed:
                    wk["passes"] += 1

        lifetime_scores.append(score)
        if d >= cutoff_30:
            recent_scores.append(score)
        elif d >= cutoff_60:
            prior_scores.append(score)

    def _series(week_buckets):
        return {
            "score": [round(sum(w["scores"]) / len(w["scores"]), 3) if w["scores"] else None
                      for w in week_buckets],
            "pass":  [round(w["passes"] / w["attempts"], 3) if w["attempts"] else None
                      for w in week_buckets],
            "attempts": [w["attempts"] for w in week_buckets],
        }

    types_out: dict = {}
    for tkey, level_map in acc.items():
        agg = _new_weeks()
        levels_out: dict = {}
        for lvl, week_buckets in level_map.items():
            levels_out[lvl] = _series(week_buckets)
            for i, w in enumerate(week_buckets):
                agg[i]["scores"].extend(w["scores"])
                agg[i]["passes"] += w["passes"]
                agg[i]["attempts"] += w["attempts"]
        ordered = dict(sorted(levels_out.items(), key=lambda kv: _LEVEL_RANK.get(kv[0], 99)))
        types_out[tkey] = {"levels": ordered, "weekly": _series(agg)}

    recent_avg = _avg(recent_scores)
    prior_avg = _avg(prior_scores)
    lifetime_avg = _avg(lifetime_scores)
    delta = (round(recent_avg - prior_avg, 3)
             if recent_avg is not None and prior_avg is not None else None)

    return {
        "weeks":        weeks,
        "week_starts":  [ws.isoformat() for ws in week_starts],
        "week_labels":  [ws.strftime("%m/%d") for ws in week_starts],
        "types":        types_out,
        "recent_avg":   recent_avg,
        "lifetime_avg": lifetime_avg,
        "delta":        delta,
        "total_attempts": len(lifetime_scores),
    }


def get_word_mastery_trend(access_code: str, weeks: int = 8) -> dict:
    """Cumulative 'words mastered' curve, weekly and latched (never decreases).

    A word counts as mastered once it reaches >= 3 attempts at >= 80% hit-rate.
    Mastery is latched (a word stays mastered once it crosses the bar), so the
    student-facing curve only ever rises. Returns the cumulative distinct count
    as of each week end over the last ``weeks`` calendar weeks.
    """
    with _conn() as conn:
        rows = conn.execute(
            "SELECT payload, ts FROM events WHERE access_code=? "
            "AND event_type IN ('phrase_attempted','paragraph_attempted','paragraph_drilled','word_attempted') "
            "ORDER BY ts ASC",
            (access_code,),
        ).fetchall()

    today = date.today()
    cur_week_start = today - timedelta(days=today.weekday())
    week_starts = [cur_week_start - timedelta(weeks=(weeks - 1 - i)) for i in range(weeks)]

    word_stats: dict = defaultdict(lambda: {"attempts": 0, "hits": 0})
    mastered: dict = {}   # word -> date first mastered

    for row in rows:
        ts = row["ts"] or ""
        if not ts:
            continue
        try:
            d = date.fromisoformat(ts[:10])
        except Exception:
            continue
        p = json.loads(row["payload"])
        for entry in p.get("word_results", []):
            word = entry[0].lower()
            s = word_stats[word]
            s["attempts"] += 1
            if entry[1]:
                s["hits"] += 1
            if (word not in mastered and s["attempts"] >= 3
                    and s["hits"] / s["attempts"] >= 0.80):
                mastered[word] = d

    cumulative = [sum(1 for md in mastered.values() if md <= ws + timedelta(days=6))
                  for ws in week_starts]
    newly = (cumulative[-1] - cumulative[-2]) if len(cumulative) >= 2 else (cumulative[-1] if cumulative else 0)
    mastered_30d = sum(1 for md in mastered.values() if md >= today - timedelta(days=30))

    return {
        "weeks":          weeks,
        "week_labels":    [ws.strftime("%m/%d") for ws in week_starts],
        "cumulative":     cumulative,
        "total_mastered": len(mastered),
        "newly_mastered": newly,
        "mastered_30d":   mastered_30d,
    }


def get_progress_summary(access_code: str) -> dict:
    """Headline KPIs for the student Home landing.

    Three confound-aware progress signals:
    - words mastered (total) + 30-day gain
    - accuracy *trend* (improving / steady / dipping), computed **within level** so
      that moving to harder material never reads as a regression
    - average session length + its 30-day change
    """
    today = date.today()
    d30 = today - timedelta(days=30)
    d60 = today - timedelta(days=60)

    mastery = get_word_mastery_trend(access_code)

    # ── Within-level accuracy trend ──────────────────────────────────────────
    with _conn() as conn:
        rows = conn.execute(
            "SELECT payload, ts FROM events WHERE access_code=? "
            "AND event_type IN ('phrase_attempted','paragraph_attempted','paragraph_drilled','word_attempted')",
            (access_code,),
        ).fetchall()

    lvl_recent: dict = defaultdict(list)
    lvl_prior: dict  = defaultdict(list)
    recent_level_attempts: dict = defaultdict(int)
    for r in rows:
        p = json.loads(r["payload"])
        score = p.get("score")
        ts = r["ts"] or ""
        if score is None or not ts:
            continue
        try:
            d = date.fromisoformat(ts[:10])
        except Exception:
            continue
        level = str(p.get("level") or "?") or "?"
        if d >= d30:
            lvl_recent[level].append(score)
            recent_level_attempts[level] += 1
        elif d >= d60:
            lvl_prior[level].append(score)

    # Attempt-weighted mean of per-level (recent − prior) deltas. Each level is
    # compared against itself, so the difficulty confound is removed.
    num = 0.0
    den = 0
    for lvl, recent in lvl_recent.items():
        prior = lvl_prior.get(lvl)
        if prior:
            num += (_avg(recent) - _avg(prior)) * len(recent)
            den += len(recent)
    acc_delta = round(num / den, 3) if den else None

    if acc_delta is None:
        acc_direction = "new"
    elif acc_delta >= 0.02:
        acc_direction = "improving"
    elif acc_delta <= -0.02:
        acc_direction = "dipping"
    else:
        acc_direction = "steady"

    acc_level = (max(recent_level_attempts.items(), key=lambda kv: kv[1])[0]
                 if recent_level_attempts else None)

    # ── Average session length + 30-day change ───────────────────────────────
    with _conn() as conn:
        srows = conn.execute(
            "SELECT event_type, payload, ts FROM events WHERE access_code=? AND ts IS NOT NULL "
            "AND event_type IN ('session_start','paragraph_started','chunk_listened',"
            "'phrase_attempted','paragraph_attempted','paragraph_drilled','word_attempted') "
            "ORDER BY ts ASC",
            (access_code,),
        ).fetchall()
    events = [(r["ts"], r["event_type"], json.loads(r["payload"])) for r in srows]
    groups = _group_events_into_sessions(events)

    recent_durs: list = []
    prior_durs: list = []
    for g in groups:
        start_ts, end_ts = g[0][0], g[-1][0]
        try:
            sd = date.fromisoformat(start_ts[:10])
            dur = (datetime.fromisoformat(end_ts.replace(' ', 'T')) -
                   datetime.fromisoformat(start_ts.replace(' ', 'T'))).total_seconds()
        except Exception:
            continue
        if sd >= d30:
            recent_durs.append(dur)
        elif sd >= d60:
            prior_durs.append(dur)

    avg_recent_min = round(sum(recent_durs) / len(recent_durs) / 60, 1) if recent_durs else None
    avg_prior_min  = round(sum(prior_durs) / len(prior_durs) / 60, 1) if prior_durs else None
    sess_delta_min = (round(avg_recent_min - avg_prior_min, 1)
                      if avg_recent_min is not None and avg_prior_min is not None else None)

    return {
        "words_mastered":        mastery["total_mastered"],
        "words_mastered_30d":    mastery["mastered_30d"],
        "accuracy_direction":    acc_direction,
        "accuracy_delta":        acc_delta,
        "accuracy_level":        acc_level,
        "avg_session_minutes":   avg_recent_min,
        "avg_session_delta_min": sess_delta_min,
        "sessions_30d":          len(recent_durs),
    }


# ── Student Home page payload ───────────────────────────────────────────────────

# Scored speaking events → the three Home KPIs. Per-sentence drills are Precision.
_HOME_SKILL = {
    "paragraph_attempted": "performance",
    "phrase_attempted":    "precision",
    "paragraph_drilled":   "precision",
    # word_attempted contributes only to word mastery (via word_results), not an accuracy skill
}
_NEXT_LEVEL = {"A1": "A2", "A2": "B1", "B1": "B2", "B2": "C1", "C1": "C2", "C2": None}


def get_home_data(access_code: str, weeks: int = 8) -> dict:
    """Everything the redesigned student Home needs, in one payload.

    Three speaking KPIs (Performance, Precision, Words mastered), each with a
    current value, recent change (Precision/Performance computed *within level*),
    a per-level weekly series for the staggered chart (Words = cumulative curve),
    a level-up flag, the default-highlight pick, and the tip signals.
    Presentation (labels, copy, chart formatters) lives in the frontend.
    """
    today = date.today()
    cur_week_start = today - timedelta(days=today.weekday())
    week_starts = [cur_week_start - timedelta(weeks=(weeks - 1 - i)) for i in range(weeks)]
    week_index = {ws: i for i, ws in enumerate(week_starts)}
    week_labels = [ws.strftime("%m/%d") for ws in week_starts]

    cutoff_30 = today - timedelta(days=30)
    cutoff_60 = today - timedelta(days=60)

    with _conn() as conn:
        rows = conn.execute(
            "SELECT event_type, payload, ts FROM events WHERE access_code=? "
            "AND event_type IN ('phrase_attempted','paragraph_attempted','paragraph_drilled','word_attempted') "
            "ORDER BY ts ASC",
            (access_code,),
        ).fetchall()

    def _new_weeks():
        return [[] for _ in range(weeks)]

    acc: dict = defaultdict(lambda: defaultdict(_new_weeks))     # skill -> level -> [week lists]
    lvl_recent: dict = defaultdict(lambda: defaultdict(list))    # skill -> level -> recent scores
    lvl_prior: dict = defaultdict(lambda: defaultdict(list))     # skill -> level -> prior scores
    recent_all: dict = defaultdict(list)                         # skill -> recent scores (all levels)
    recent_lvl_attempts: dict = defaultdict(Counter)            # skill -> Counter(level)

    word_stats: dict = defaultdict(lambda: {"attempts": 0, "hits": 0})
    mastered: dict = {}                                          # word -> datetime mastered
    speaking_rows: list = []                                     # (datetime, level) for phrase/para/drill
    scored_rows: list = []                                       # (datetime, skill, level, score) — for session-axis chart
    all_dts: list = []                                           # every event datetime — defines practice sessions
    attempt_week_starts: set = set()                            # distinct ISO weeks with any activity
    last_ts = None

    for r in rows:
        p = json.loads(r["payload"]); et = r["event_type"]; ts = r["ts"] or ""
        if not ts:
            continue
        try:
            d = date.fromisoformat(ts[:10])
        except Exception:
            continue
        last_ts = ts
        try:
            dt_full = datetime.fromisoformat(ts.replace(" ", "T"))
        except Exception:
            dt_full = None
        if dt_full is not None:
            all_dts.append(dt_full)
        attempt_week_starts.add(d - timedelta(days=d.weekday()))

        # Word mastery — replay word_results from ANY scored event
        for entry in p.get("word_results", []):
            w = entry[0].lower(); s = word_stats[w]; s["attempts"] += 1
            if entry[1]:
                s["hits"] += 1
            if w not in mastered and s["attempts"] >= 3 and s["hits"] / s["attempts"] >= 0.80:
                mastered[w] = dt_full or datetime.combine(d, datetime.min.time())

        skill = _HOME_SKILL.get(et)
        if skill is None:
            continue
        score = p.get("score")
        if score is None:
            continue
        level = str(p.get("level") or "?") or "?"
        if dt_full is not None:
            speaking_rows.append((dt_full, level))
            scored_rows.append((dt_full, skill, level, score))
        wi = week_index.get(d - timedelta(days=d.weekday()))
        if wi is not None:
            acc[skill][level][wi].append(score)
        if d >= cutoff_30:
            lvl_recent[skill][level].append(score)
            recent_all[skill].append(score)
            recent_lvl_attempts[skill][level] += 1
        elif d >= cutoff_60:
            lvl_prior[skill][level].append(score)

    # ── Level history (sessions → dominant level) for level-up + tip signals ──
    gap = timedelta(minutes=SESSION_GAP_MINUTES)
    session_levels: list = []   # Counter per session, chronological
    prev_dt = None
    for dt, lvl in speaking_rows:
        if prev_dt is None or (dt - prev_dt) > gap:
            session_levels.append(Counter())
        session_levels[-1][lvl] += 1
        prev_dt = dt
    session_dom = [c.most_common(1)[0][0] for c in session_levels if c]
    dom_counts = Counter(session_dom)

    levels_seen = [L for L in dom_counts if L != "?"]
    first_level = min(levels_seen, key=lambda L: _LEVEL_RANK.get(L, 99)) if levels_seen else None
    current_level = session_dom[-1] if session_dom else None
    last_level = current_level

    # Level-up: current level is dominant in >=2 sessions AND higher than an
    # established (>=2 session) lower level.
    new_level = None
    established = None
    if current_level and dom_counts.get(current_level, 0) >= 2:
        lower = [L for L in levels_seen
                 if _LEVEL_RANK.get(L, 0) < _LEVEL_RANK.get(current_level, 0) and dom_counts.get(L, 0) >= 2]
        if lower:
            established = max(lower, key=lambda L: _LEVEL_RANK.get(L, 0))
            new_level = current_level

    # Ready to level up: settled at current (>=4 dominant sessions), high recent
    # accuracy, no higher level attempted yet, and a next level exists.
    ready = False
    next_level = _NEXT_LEVEL.get(current_level) if current_level else None
    if current_level and not new_level and next_level and dom_counts.get(current_level, 0) >= 4:
        higher_tried = any(_LEVEL_RANK.get(L, 0) > _LEVEL_RANK.get(current_level, 0) for L in levels_seen)
        cur_recent = (lvl_recent["performance"].get(current_level, []) +
                      lvl_recent["precision"].get(current_level, []))
        if not higher_tried and cur_recent and _avg(cur_recent) >= 0.85:
            ready = True

    # ── Per-skill KPI assembly (performance, precision) ──
    def _weighted_delta(skill):
        num = 0.0; den = 0
        for lvl, recent in lvl_recent[skill].items():
            prior = lvl_prior[skill].get(lvl)
            if recent and prior:
                num += (_avg(recent) - _avg(prior)) * len(recent); den += len(recent)
        return round(num / den, 3) if den else None

    def _trend(delta):
        if delta is None or abs(delta) < 0.005:
            return None
        n = abs(round(delta * 100))
        return {"dir": "up" if delta > 0 else "down", "text": ("+" if delta > 0 else "−") + str(n) + " pts"}

    def _clamp01(v):
        return max(0.0, min(1.0, v))

    kpis: dict = {}
    for skill in ("performance", "precision"):
        levels_out = []
        for lvl in sorted(acc[skill].keys(), key=lambda L: _LEVEL_RANK.get(L, 99)):
            pts = [(round(sum(w) / len(w), 3) if w else None) for w in acc[skill][lvl]]
            levels_out.append({"level": lvl, "points": pts})
        recent = recent_all[skill]
        value = _avg(recent)
        delta = _weighted_delta(skill)
        kpis[skill] = {
            "value": value,
            "trend": _trend(delta),
            "momentum": _clamp01((delta or 0) / 0.10) if delta and delta > 0 else 0.0,
            "new_level": new_level,
            "levels": levels_out,
            "has_data": bool(levels_out),
        }

    # ── Words mastered ──
    cumulative = [sum(1 for md in mastered.values() if md.date() <= ws + timedelta(days=6)) for ws in week_starts]
    gain_30d = sum(1 for md in mastered.values() if md.date() >= cutoff_30)
    gain_prev_30d = sum(1 for md in mastered.values() if cutoff_60 <= md.date() < cutoff_30)
    kpis["words"] = {
        "total": len(mastered),
        "recent_gain": gain_30d,
        "trend": ({"dir": "up", "text": "+" + str(gain_30d)} if gain_30d > 0 else None),
        "momentum": _clamp01(gain_30d / 20.0),
        "cumulative": cumulative,
        "has_data": len(mastered) > 0,
    }

    # ── Adaptive chart axis ──────────────────────────────────────────────
    # Scale the x-axis to how long the student has been practising so the chart
    # is never just 2-3 dots stretched across the full page width:
    #   • started <4 weeks ago → one point per ACTIVE DAY (per session if only a
    #     single day has data, so a heavy first day still shows a real curve)
    #   • >=4 weeks ago        → weekly, trimmed to first active week..now (<=8)
    # Only the chart series + x labels switch; scalar KPIs (value/trend/momentum
    # /level-up) and the tip signals stay computed over the 30/60-day windows.
    axis = "week"
    labels = week_labels
    valid_dts = sorted(dt for dt in all_dts if dt is not None)
    first_ws = min(attempt_week_starts) if attempt_week_starts else None
    span_weeks = ((cur_week_start - first_ws).days // 7 + 1) if first_ws else 0

    def _level_series(nbins, idx_of):
        """Per-skill level-split series given a bucket count and a dt→bin fn."""
        out = {}
        for skill in ("performance", "precision"):
            lvls: dict = defaultdict(lambda: [[] for _ in range(nbins)])
            for dt, sk, lvl, score in scored_rows:
                if sk != skill:
                    continue
                bi = idx_of(dt)
                if bi is not None:
                    lvls[lvl][bi].append(score)
            out[skill] = [
                {"level": lvl,
                 "points": [(round(sum(w) / len(w), 3) if w else None) for w in lvls[lvl]]}
                for lvl in sorted(lvls.keys(), key=lambda L: _LEVEL_RANK.get(L, 99))
            ]
        return out

    if first_ws and span_weeks < 4 and valid_dts:
        active_days = sorted({dt.date() for dt in valid_dts})[-30:]
        if len(active_days) >= 2:
            day_index = {d: i for i, d in enumerate(active_days)}
            first_day = active_days[0]
            series = _level_series(
                len(active_days),
                lambda dt: day_index.get(dt.date()) if dt.date() >= first_day else None)
            kpis["words"]["cumulative"] = [
                sum(1 for md in mastered.values() if md.date() <= d) for d in active_days
            ]
            labels = [d.strftime("%-m/%-d") for d in active_days]
            axis = "day"
        else:
            # Single active day → fall back to one point per practice session so a
            # heavy first day still shows a curve, not a lone dot.
            sessions: list = []
            for dt in valid_dts:
                if not sessions or (dt - sessions[-1][1]) > gap:
                    sessions.append([dt, dt])
                else:
                    sessions[-1][1] = dt
            sessions = sessions[-10:]
            first_start = sessions[0][0]

            def _sidx(dt):
                if dt < first_start:
                    return None
                for i in range(len(sessions) - 1, -1, -1):
                    if dt >= sessions[i][0]:
                        return i
                return 0

            series = _level_series(len(sessions), _sidx)
            kpis["words"]["cumulative"] = [
                sum(1 for md in mastered.values() if md <= end) for (_s, end) in sessions
            ]
            labels = [s.strftime("%a %-I%p").lower() for (s, _e) in sessions]
            axis = "session"

        for skill in ("performance", "precision"):
            kpis[skill]["levels"] = series[skill]
            kpis[skill]["has_data"] = any(
                any(p is not None for p in s["points"]) for s in series[skill])

    # Weekly path: trim leading empty weeks so a student who started N (<8) weeks
    # ago shows ~N columns, not 8 with blank leaders. The window runs from their
    # first in-window active week to now (capped at 8); a gap mid-window stays
    # visible (a real "you paused here" signal).
    if axis == "week":
        in_window = [week_index[w] for w in attempt_week_starts if w in week_index]
        start_i = min(in_window) if in_window else 0
        if start_i > 0:
            labels = week_labels[start_i:]
            for skill in ("performance", "precision"):
                for lv in kpis[skill]["levels"]:
                    lv["points"] = lv["points"][start_i:]
            kpis["words"]["cumulative"] = kpis["words"]["cumulative"][start_i:]

    # ── Default highlight = biggest momentum; fallback fixed order ──
    order = ["performance", "precision", "words"]
    movers = [k for k in order if kpis[k]["momentum"] > 0]
    default_key = max(movers, key=lambda k: kpis[k]["momentum"]) if movers else \
        next((k for k in order if kpis[k]["has_data"]), "performance")

    # ── Tip signals ──
    has_speaking = any(kpis[s]["has_data"] for s in ("performance", "precision"))
    is_new = not has_speaking and kpis["words"]["total"] == 0
    gap_days = None
    if last_ts:
        try:
            gap_days = (today - date.fromisoformat(last_ts[:10])).days
        except Exception:
            pass
    top = max(("performance", "precision"), key=lambda s: kpis[s]["momentum"])
    # Constructive dip: a skill whose within-level trend is down and nothing is climbing
    dip = None
    if not movers:
        for s in ("performance", "precision"):
            t = kpis[s]["trend"]
            if t and t["dir"] == "down":
                dip = s; break

    signals = {
        "new": is_new,
        "first_level": first_level,
        "current_level": current_level,
        "ready_to_level_up": ready,
        "next_level": next_level if ready else None,
        "returning": bool(gap_days is not None and gap_days >= 5 and has_speaking),
        "last_level": last_level,
        "gap_days": gap_days,
        "new_words": gain_30d,
        "top_skill": top,
        "top_trend_text": (kpis[top]["trend"] or {}).get("text"),
        "dip": dip,
    }

    return {
        "week_labels": week_labels,
        "labels": labels,
        "axis": axis,
        "kpis": kpis,
        "default_key": default_key,
        "signals": signals,
    }
