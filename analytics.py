import os
import sqlite3
import json
import secrets
import string
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict
from typing import Any, Optional

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

        # 7d: distinct sessions
        s7_rows = conn.execute(
            "SELECT access_code, COUNT(DISTINCT visit_id) AS sessions "
            "FROM events WHERE event_type='session_start' AND date(ts) >= ? "
            "GROUP BY access_code",
            (since_7d,),
        ).fetchall()
        sessions_7d = {r["access_code"]: r["sessions"] for r in s7_rows}

        # 7d: total practice duration (seconds)
        dur_rows = conn.execute(
            "SELECT access_code, "
            "SUM(CAST(json_extract(payload,'$.duration_seconds') AS INTEGER)) AS total_sec "
            "FROM events WHERE event_type='session_end' AND date(ts) >= ? "
            "GROUP BY access_code",
            (since_7d,),
        ).fetchall()
        duration_7d = {r["access_code"]: int(r["total_sec"] or 0) for r in dur_rows}

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

        # 30d: sessions (for roster KPI)
        s30_rows = conn.execute(
            "SELECT access_code, COUNT(DISTINCT visit_id) AS sessions "
            "FROM events WHERE event_type='session_start' AND date(ts) >= ? "
            "GROUP BY access_code",
            (since_30d,),
        ).fetchall()
        sessions_30d = {r["access_code"]: r["sessions"] for r in s30_rows}

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
            "sessions_30d":            sessions_30d.get(code, 0),
            "topics":                  topics_map.get(code, []),
            "avg_score":               acc.get("score"),
            "avg_score_7d":            acc.get("avg_score_7d"),
            "avg_score_since_lesson":  avg_score_since_lesson,
            "health":                  health,
        })

    result.sort(key=lambda x: (x["days_until_next"] is None, x["days_until_next"] or 0))
    return result


def get_practice_since(access_code: str, since: date) -> dict:
    """Aggregate practice events strictly after `since` date."""
    since_str = since.isoformat()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT event_type, payload, ts, visit_id, session_id FROM events "
            "WHERE access_code=? AND date(ts) > ?",
            (access_code, since_str),
        ).fetchall()

    visit_ids: set = set()
    days_active: set = set()
    total_attempts = 0
    scores: list = []
    word_stats: dict = defaultdict(lambda: {"attempts": 0, "misses": 0})
    session_durations: dict = {}  # visit_id → duration_seconds

    for row in rows:
        t  = row["event_type"]
        p  = json.loads(row["payload"])
        ts = row["ts"] or ""
        if ts:
            days_active.add(ts[:10])

        if t == "session_start":
            visit_ids.add(row["visit_id"] or row["session_id"])

        if t == "session_end":
            dur = p.get("duration_seconds")
            vid = row["visit_id"] or row["session_id"]
            if dur is not None:
                session_durations[vid] = int(dur)

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

    durations = [session_durations[v] for v in visit_ids if v in session_durations]
    total_seconds = sum(durations) if durations else None
    avg_seconds   = round(total_seconds / len(durations)) if durations else None

    return {
        "sessions": len(visit_ids),
        "days_active": len(days_active),
        "total_attempts": total_attempts,
        "avg_score": _avg(scores),
        "struggles": struggles[:8],
        "total_seconds": total_seconds,
        "avg_seconds": avg_seconds,
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
    """Return per-visit summary rows, most recent first."""
    with _conn() as conn:
        rows = conn.execute(
            """SELECT session_id, visit_id, event_type, payload, ts
               FROM events
               WHERE access_code=?
               ORDER BY ts ASC""",
            (access_code,),
        ).fetchall()

    # ── Phase 1: build visits from events that carry a visit_id ───────────────
    visits: dict = defaultdict(lambda: {
        "visit_id": None, "started_at": None, "ended_at": None,
        "duration_seconds": None, "phrase_attempts": 0, "phrase_scores": [],
        "word_attempts": 0, "word_scores": [],
        "paragraph_attempts": 0, "paragraph_scores": [], "paragraph_drills": 0, "paragraph_drill_scores": [],
        "word_set": set(),
    })

    def _collect_words(v: dict, p: dict):
        for wr in p.get("word_results", []):
            if wr and wr[0]:
                v["word_set"].add(wr[0].lower())

    for row in rows:
        if not row["visit_id"]:
            continue
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
        elif t == "word_attempted":
            v["word_attempts"] += 1
            if p.get("score") is not None:
                v["word_scores"].append(p["score"])
        elif t == "phrase_attempted":
            v["phrase_attempts"] += 1
            if p.get("score") is not None:
                v["phrase_scores"].append(p["score"])
            _collect_words(v, p)
        elif t == "paragraph_attempted":
            v["paragraph_attempts"] += 1
            if p.get("score") is not None:
                v["paragraph_scores"].append(p["score"])
            _collect_words(v, p)
        elif t == "paragraph_drilled":
            v["paragraph_drills"] += 1
            if p.get("score") is not None:
                v["paragraph_drill_scores"].append(p["score"])
            _collect_words(v, p)

    # ── Phase 2: assign orphan practice events to visits by timestamp ─────────
    # Build a sorted list of (start_ts, visit_id). Orphan events belong to the
    # visit whose session_start is the latest one that precedes the event's ts.
    # (SQLite stores timestamps as "YYYY-MM-DD HH:MM:SS" — lexicographic sort works.)
    windows = sorted(
        [(v["started_at"], vid) for vid, v in visits.items() if v["started_at"]],
        key=lambda x: x[0],
    )

    def _visit_for_ts(ts: str) -> Optional[str]:
        if not ts or not windows:
            return None
        matched = None
        for start, vid in windows:
            if start <= ts:
                matched = vid
            else:
                break
        return matched

    for row in rows:
        if row["visit_id"]:
            continue  # already handled in phase 1
        t = row["event_type"]
        if t not in ("word_attempted", "phrase_attempted", "paragraph_attempted", "paragraph_drilled"):
            continue
        ts  = row["ts"]
        p   = json.loads(row["payload"])
        vid = _visit_for_ts(ts)
        if vid is None:
            continue
        v = visits[vid]
        if t == "word_attempted":
            v["word_attempts"] += 1
            if p.get("score") is not None:
                v["word_scores"].append(p["score"])
        elif t == "phrase_attempted":
            v["phrase_attempts"] += 1
            if p.get("score") is not None:
                v["phrase_scores"].append(p["score"])
            _collect_words(v, p)
        elif t == "paragraph_attempted":
            v["paragraph_attempts"] += 1
            if p.get("score") is not None:
                v["paragraph_scores"].append(p["score"])
            _collect_words(v, p)
        elif t == "paragraph_drilled":
            v["paragraph_drills"] += 1
            if p.get("score") is not None:
                v["paragraph_drill_scores"].append(p["score"])
            _collect_words(v, p)

    # ── Phase 3: compute new vs revisited words per visit ─────────────────────
    _PASS_PARA   = 0.70
    _PASS_PHRASE = 0.90
    seen_words: set = set()
    ordered_visits = sorted(
        [v for v in visits.values() if v["started_at"]],
        key=lambda x: x["started_at"],
    )
    for v in ordered_visits:
        ws = v["word_set"]
        v["words_new"]       = len(ws - seen_words)
        v["words_revisited"] = len(ws & seen_words)
        seen_words |= ws

    results = []
    for v in visits.values():
        if not v["started_at"]:
            continue
        results.append({
            "visit_id":                  v["visit_id"],
            "started_at":                v["started_at"],
            "ended_at":                  v["ended_at"],
            "duration_seconds":          v["duration_seconds"],
            "word_attempts":             v["word_attempts"],
            "words_new":                 v.get("words_new", 0),
            "words_revisited":           v.get("words_revisited", 0),
            "phrase_attempts":           v["phrase_attempts"],
            "phrase_passed":             sum(1 for s in v["phrase_scores"] if s >= _PASS_PHRASE),
            "avg_phrase_score":          _avg(v["phrase_scores"]),
            "paragraph_attempts":        v["paragraph_attempts"],
            "paragraph_passed":          sum(1 for s in v["paragraph_scores"] if s >= _PASS_PARA),
            "avg_paragraph_score":       _avg(v["paragraph_scores"]),
            "paragraph_drills":          v["paragraph_drills"],
            "avg_paragraph_drill_score": _avg(v["paragraph_drill_scores"]),
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
