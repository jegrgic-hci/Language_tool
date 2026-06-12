"""
Seed 7 fake students + event history for dashboard design work.
Run once:  python seed_demo_data.py
Safe to re-run — uses INSERT OR IGNORE for students and skips if teacher email exists.
"""
import sqlite3, json, random, string, secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

DB_PATH = Path("data/analytics.db")
random.seed(42)


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


# ── helpers ──────────────────────────────────────────────────────────────────

def ts(days_ago: float, hour: int = 14, minute: int = 0) -> str:
    d = datetime.now() - timedelta(days=days_ago)
    return d.replace(hour=hour, minute=minute, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def vid() -> str:
    return str(__import__("uuid").uuid4())


def word_results(words: List[str], accuracy: float, struggles: Optional[List[str]] = None) -> list:
    struggles = [w.lower() for w in (struggles or [])]
    alts = {"je": "j'", "tu": "t'", "ne": "n'", "le": "l'", "la": "l'",
            "voudrais": "voulais", "tranquillement": "tranquilement",
            "maintenant": "maintenent", "quelqu'un": "quelqu'un",
            "toujours": "tjours", "comprendre": "comprenre",
            "vraiment": "vraiement", "surtout": "surto"}
    out = []
    for w in words:
        p = 0.25 if w.lower() in struggles else accuracy
        matched = random.random() < p
        said = w if matched else alts.get(w.lower(), w[:-1] if len(w) > 3 else "")
        out.append([w, matched, said])
    return out


def avg_score(wr: list) -> float:
    if not wr:
        return 0.5
    return round(sum(1 for x in wr if x[1]) / len(wr), 3)


def insert_event(c, session_id: str, access_code: str, event_type: str,
                 payload: dict, timestamp: str, visit_id: Optional[str] = None):
    c.execute(
        "INSERT INTO events (session_id, access_code, event_type, payload, ts, visit_id) VALUES (?,?,?,?,?,?)",
        (session_id, access_code, event_type, json.dumps(payload), timestamp, visit_id),
    )


# ── sentence pools ────────────────────────────────────────────────────────────

SENTENCES = {
    "A1": [
        ["je", "voudrais", "un", "café"],
        ["tu", "parles", "bien", "français"],
        ["il", "est", "à", "Marseille"],
        ["nous", "allons", "au", "marché"],
        ["c'est", "vraiment", "bien"],
    ],
    "A2": [
        ["je", "ne", "comprends", "pas", "toujours"],
        ["voudrais", "tu", "venir", "avec", "nous"],
        ["maintenant", "il", "faut", "partir"],
        ["quelqu'un", "frappe", "à", "la", "porte"],
        ["c'est", "tranquillement", "qu'on", "avance"],
    ],
    "B1": [
        ["je", "voudrais", "vraiment", "comprendre", "surtout", "maintenant"],
        ["tranquillement", "on", "trouve", "toujours", "une", "solution"],
        ["quelqu'un", "doit", "bien", "savoir", "ce", "qui", "se", "passe"],
        ["il", "faut", "que", "tu", "comprennes", "vraiment"],
        ["maintenant", "ou", "jamais", "c'est", "le", "moment"],
    ],
}

PARA_IDS = ["para_marseille_01", "para_marseille_02", "para_cafe_01",
            "para_metro_01", "para_daily_01"]

# topic carried on paragraph_started (joined back to attempts via paragraph_id)
TOPIC_BY_PARA = {
    "para_marseille_01": "vie à Marseille",
    "para_marseille_02": "directions",
    "para_cafe_01":      "au café",
    "para_metro_01":     "transports",
    "para_daily_01":     "vie quotidienne",
}
# topic carried directly on phrase_attempted
PHRASE_TOPICS = ["au restaurant", "au téléphone", "les courses", "la météo", "au café"]


def simulate_session(c, access_code: str, session_id: str, days_ago: float,
                     level: str, accuracy: float, struggles: list[str],
                     n_phrases: int = 4, n_chunks: int = 3, n_drills: int = 6,
                     duration: int = 1200):
    """Write one session's worth of events."""
    visit_id = vid()
    start_h = random.randint(9, 19)
    start_m = random.randint(0, 30)
    start_ts = ts(days_ago, start_h, start_m)
    end_mins = start_m + duration // 60
    end_h = start_h + end_mins // 60
    end_ts = ts(days_ago, min(end_h, 23), end_mins % 60)

    insert_event(c, session_id, access_code, "session_start", {}, start_ts, visit_id)

    sents = SENTENCES.get(level, SENTENCES["A2"])
    para  = random.choice(PARA_IDS)
    para_topic = TOPIC_BY_PARA.get(para, "vie quotidienne")

    phrase_topic = random.choice(PHRASE_TOPICS)
    for i in range(n_phrases):
        wr = word_results(random.choice(sents), accuracy, struggles)
        insert_event(c, session_id, access_code, "phrase_attempted",
                     {"level": level, "topic": phrase_topic, "score": avg_score(wr), "word_results": wr},
                     ts(days_ago, start_h, start_m + 2 + i), visit_id)

    # paragraph_started carries the topic for all chunk/drill attempts below
    insert_event(c, session_id, access_code, "paragraph_started",
                 {"paragraph_id": para, "level": level, "topic": para_topic,
                  "sentence_count": n_chunks * 3},
                 ts(days_ago, start_h, start_m + 4), visit_id)

    for ci in range(n_chunks):
        # student replays the chunk audio 1–3× before attempting (listen-to-speak)
        for li in range(random.randint(1, 3)):
            insert_event(c, session_id, access_code, "chunk_listened",
                         {"paragraph_id": para, "chunk_index": ci, "chunk_size": 1},
                         ts(days_ago, start_h, start_m + 5 + ci * 2), visit_id)
        for attempt in range(1, random.randint(2, 4)):
            # score improves slightly on retries
            eff_acc = min(accuracy + 0.05 * (attempt - 1), 0.97)
            wr = word_results(random.choice(sents), eff_acc, struggles)
            insert_event(c, session_id, access_code, "chunk_attempted",
                         {"level": level, "score": avg_score(wr), "word_results": wr,
                          "paragraph_id": para, "chunk_index": ci, "attempt_number": attempt},
                         ts(days_ago, start_h, start_m + 5 + ci * 2 + attempt), visit_id)

    for si in range(n_drills):
        ci_d = si // 3
        for attempt in range(1, random.randint(2, 5)):
            eff_acc = min(accuracy + 0.04 * (attempt - 1), 0.97)
            wr = word_results(random.choice(sents), eff_acc, struggles)
            insert_event(c, session_id, access_code, "sentence_drilled",
                         {"level": level, "score": avg_score(wr), "word_results": wr,
                          "paragraph_id": para, "chunk_index": ci_d,
                          "sentence_index": si % 3, "attempt_number": attempt},
                         ts(days_ago, start_h, start_m + 15 + si + attempt), visit_id)

    insert_event(c, session_id, access_code, "shadowing_time",
                 {"duration_seconds": duration}, end_ts, visit_id)
    insert_event(c, session_id, access_code, "session_end",
                 {"duration_seconds": duration}, end_ts, visit_id)


# ── student definitions ───────────────────────────────────────────────────────
# Each entry: (access_code, name, email, lesson_days, lesson_time, notes,
#              session_schedule, level, base_accuracy, struggle_words)
#
# session_schedule: list of (days_ago, duration_secs)

STUDENTS = [
    # 1 — Star student. Consistent, high scores, practises every day between lessons.
    {
        "code":         "marie1",
        "name":         "Marie Dupont",
        "email":        "marie.dupont@example.com",
        "lesson_days":  ["Mon", "Wed", "Fri"],
        "lesson_time":  "10:00",
        "notes":        "Motivated learner, wants to pass DELF B2. Strong ear for rhythm.",
        "level":        "B1",
        "accuracy":     0.81,
        "struggles":    ["tranquillement", "quelqu'un"],
        "sessions": [
            (0.5, 1400), (2, 1100), (3, 1300), (5, 900), (7, 1500),
            (9, 1000), (10, 1200), (12, 1100), (14, 1400), (16, 800),
            (18, 1300), (21, 1000), (24, 1200), (26, 900),
        ],
    },

    # 2 — Solid, improving. Practises Tue/Thu, gaining confidence.
    {
        "code":         "thomas",
        "name":         "Thomas Bernard",
        "email":        "thomas.bernard@example.com",
        "lesson_days":  ["Tue", "Thu"],
        "lesson_time":  "18:30",
        "notes":        "Software engineer, travels to Lyon for work. Needs professional French.",
        "level":        "A2",
        "accuracy":     0.68,
        "struggles":    ["voudrais", "maintenant", "toujours"],
        "sessions": [
            (1, 1000), (3, 900), (5, 1100), (8, 800),
            (10, 950), (13, 1000), (15, 900), (18, 700),
        ],
    },

    # 3 — Struggling. Rarely practises between lessons, low scores.
    {
        "code":         "sophie",
        "name":         "Sophie Martin",
        "email":        "sophie.martin@example.com",
        "lesson_days":  ["Mon", "Wed"],
        "lesson_time":  "09:00",
        "notes":        "Busy schedule, 2 young kids. Finds pronunciation very hard. Needs encouragement.",
        "level":        "A1",
        "accuracy":     0.41,
        "struggles":    ["tranquillement", "comprendre", "vraiment", "toujours", "voudrais"],
        "sessions": [
            (16, 600), (22, 700), (28, 550), (35, 650), (41, 500),
        ],
    },

    # 4 — Brand-new. Only 3 sessions, no history yet to establish patterns.
    {
        "code":         "julien",
        "name":         "Julien Roux",
        "email":        "julien.roux@example.com",
        "lesson_days":  ["Fri"],
        "lesson_time":  "17:00",
        "notes":        "Complete beginner. Moved to Marseille last month for work.",
        "level":        "A1",
        "accuracy":     0.58,
        "struggles":    ["voudrais", "quelqu'un"],
        "sessions": [
            (2, 900), (8, 700), (15, 650),
        ],
    },

    # 5 — Inconsistent. Some great sessions, some missed weeks.
    {
        "code":         "camille",
        "name":         "Camille Blanc",
        "email":        "camille.blanc@example.com",
        "lesson_days":  ["Mon", "Thu"],
        "lesson_time":  "19:00",
        "notes":        "Artist, very creative. Inconsistent practice. Good intuition for rhythm.",
        "level":        "A2",
        "accuracy":     0.61,
        "struggles":    ["maintenant", "quelqu'un", "tranquillement"],
        "sessions": [
            (8, 1300), (9, 1100),   # burst of 2 close together
            (20, 700),              # then nothing for 11 days
            (22, 600), (23, 900),   # another burst
            (36, 800),              # isolated session weeks ago
            (37, 750),
        ],
    },

    # 6 — Long-timer, plateaued. Many sessions but score not climbing.
    {
        "code":         "antoine",
        "name":         "Antoine Petit",
        "email":        "antoine.petit@example.com",
        "lesson_days":  ["Mon", "Wed", "Fri"],
        "lesson_time":  "08:00",
        "notes":        "Retired teacher, very diligent. Drilling same material — needs new challenge.",
        "level":        "B1",
        "accuracy":     0.71,
        "struggles":    ["quelqu'un", "toujours"],
        "sessions": [
            (1, 1500), (3, 1400), (5, 1600), (7, 1300),
            (8, 1500), (10, 1400), (12, 1600), (14, 1200),
            (15, 1500), (17, 1300), (19, 1400), (21, 1100),
            (22, 1500), (24, 1400), (26, 1200), (28, 1300),
            (29, 1500), (31, 1400),
        ],
        "accuracy_variance": 0.02,   # low variance = plateaued
    },

    # 7 — No lesson schedule set. Freelance learner, active but unstructured.
    {
        "code":         "lea001",
        "name":         "Léa Moreau",
        "email":        "lea.moreau@example.com",
        "lesson_days":  [],
        "lesson_time":  "",
        "notes":        "Self-directed learner. No formal lessons — just using the app independently.",
        "level":        "A2",
        "accuracy":     0.66,
        "struggles":    ["voudrais", "vraiment"],
        "sessions": [
            (2, 1000), (4, 900), (7, 1100), (9, 800),
            (12, 950), (16, 800), (20, 900), (23, 700), (27, 850),
        ],
    },
]


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    with conn() as c:
        # Teacher
        existing = c.execute("SELECT id FROM teachers WHERE email=?", ("jegrgic@gmail.com",)).fetchone()
        if existing:
            teacher_id = existing[0]
            print(f"Teacher already exists (id={teacher_id})")
        else:
            cur = c.execute(
                "INSERT INTO teachers (name, email, key) VALUES (?,?,?)",
                ("Joseph Grgic", "jegrgic@gmail.com", "teach123"),
            )
            teacher_id = cur.lastrowid
            print(f"Created teacher id={teacher_id}")

        for s in STUDENTS:
            code = s["code"]
            existing_s = c.execute("SELECT access_code FROM students WHERE access_code=?", (code,)).fetchone()
            if existing_s:
                print(f"  Student {code} already exists — skipping student + event insert")
                continue

            c.execute(
                "INSERT INTO students (access_code, teacher_id, name, email, lesson_days, lesson_time, notes) "
                "VALUES (?,?,?,?,?,?,?)",
                (code, teacher_id, s["name"], s["email"],
                 json.dumps(s["lesson_days"]), s["lesson_time"], s["notes"]),
            )

            variance = s.get("accuracy_variance", 0.09)
            for i, (days, duration) in enumerate(s["sessions"]):
                session_id = f"sess_{code}_{i:02d}"
                # small per-session accuracy jitter
                jitter = random.gauss(0, variance)
                sess_acc = max(0.15, min(0.97, s["accuracy"] + jitter))
                # gradually increase accuracy over time to show learning curve
                # (older sessions = lower accuracy)
                recency_boost = (len(s["sessions"]) - i) * -0.003
                sess_acc = max(0.15, min(0.97, sess_acc + recency_boost))

                # vary number of events slightly
                n_phrases = random.randint(3, 6)
                n_chunks  = random.randint(2, 4)
                n_drills  = random.randint(4, 8)

                simulate_session(
                    c, code, session_id, days, s["level"], sess_acc, s["struggles"],
                    n_phrases=n_phrases, n_chunks=n_chunks, n_drills=n_drills,
                    duration=duration,
                )

            # count events inserted
            event_count = c.execute("SELECT COUNT(*) FROM events WHERE access_code=?", (code,)).fetchone()[0]
            print(f"  Seeded {s['name']} ({code}): {len(s['sessions'])} sessions, {event_count} events")

    print("\nDone. Open http://127.0.0.1:8000/analytics/dashboard?key=<your-key> to see the dashboard.")


if __name__ == "__main__":
    main()
