"""
Dev test account seeder.
Run once (or re-run safely — skips accounts that already exist).

Creates:
  teacher@dev.test     / devtest123   role=teacher
  student_t@dev.test   / devtest123   role=student_teacher  (linked to test teacher)
  student_s@dev.test   / devtest123   role=student_solo

Usage:
  cd /Users/josephgrgic/Documents/GitHub/Language_tool
  source .venv/bin/activate
  python seed_test.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import analytics as _analytics
import auth as _auth

_analytics.init_db()

DEV_PASSWORD = "devtest123"

ACCOUNTS = [
    {
        "email": "teacher@dev.test",
        "role": "teacher",
        "label": "Teacher",
        "dest": "/analytics",
    },
    {
        "email": "student_t@dev.test",
        "role": "student_teacher",
        "label": "Student (teacher-linked)",
        "dest": "/",
    },
    {
        "email": "student_s@dev.test",
        "role": "student_solo",
        "label": "Student (solo)",
        "dest": "/",
    },
]


def seed():
    teacher_id = None
    results = []

    for acct in ACCOUNTS:
        existing = _analytics.get_user_by_email(acct["email"])
        if existing:
            results.append((acct["label"], acct["email"], "already exists", existing["id"]))
            if acct["role"] == "teacher":
                teacher_id = existing["id"]
            continue

        kwargs = dict(
            role=acct["role"],
            email=acct["email"],
            password_hash=_auth.hash_password(DEV_PASSWORD),
            access_code=_auth.generate_access_code(),
        )
        if acct["role"] == "student_teacher" and teacher_id:
            kwargs["teacher_id"] = teacher_id

        user = _analytics.create_user(**kwargs)
        if acct["role"] == "teacher":
            teacher_id = user["id"]
        results.append((acct["label"], acct["email"], "created", user["id"]))

    print()
    print("── Dev test accounts ─────────────────────────────────────────")
    print(f"  password for all:  {DEV_PASSWORD}")
    print()
    for label, email, status, uid in results:
        flag = "✓" if status == "created" else "–"
        print(f"  {flag}  [{label:28s}]  {email:26s}  (id={uid}, {status})")
    print()
    print("  Launch the dev switcher at:  http://127.0.0.1:8000/dev")
    print("─────────────────────────────────────────────────────────────")
    print()


if __name__ == "__main__":
    seed()
