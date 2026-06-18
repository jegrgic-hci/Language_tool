# Admin System Reference

## Overview

The super admin hub is a separate view from the teacher analytics dashboard. It is accessed at `/admin` and is restricted to accounts with `role = super_admin`. It gives a platform-wide view of users, activity, feature usage, and scoring metrics — plus direct access to the main tool for personal practice.

---

## Super Admin Account

The super admin account is seeded automatically on server startup from two environment variables:

| Env Var | Purpose |
|---|---|
| `SUPER_ADMIN_EMAIL` | Login email for the super admin account |
| `SUPER_ADMIN_PASSWORD` | Login password (hashed on startup via bcrypt) |

If these are not set, no super admin account is created and a warning is logged. Set them in Render → Environment before deploying.

The seeding function (`analytics.seed_super_admin`) is idempotent — it only creates the account if no `super_admin` row exists. It does not overwrite an existing account.

---

## Roles

| Role | Access |
|---|---|
| `super_admin` | Full platform access — admin hub + all analytics + teacher routes |
| `teacher` | Teacher analytics dashboard, own students only |
| `student_teacher` | Main tool only; linked to a teacher |
| `student_solo` | Main tool only; no teacher |

`super_admin` bypasses all teacher-scoped filters — it can read any student's data and manage all users.

---

## Admin Routes (all require `super_admin` JWT)

| Method | Route | Purpose |
|---|---|---|
| GET | `/admin/users` | List all users across all roles |
| GET | `/admin/teachers` | List teacher accounts only |
| POST | `/admin/teachers` | Create a new teacher account |
| PUT | `/admin/users/{user_id}` | Update role or active status of any user |
| GET | `/admin/platform-stats?dataset=` | Platform-wide KPIs, sparkline, exercise breakdown, top users |
| GET | `/admin/user-hierarchy` | All teachers with their linked students, annotated with event counts |
| GET | `/admin/feature-usage` | Per-exercise stats for the last 30 days (all exercises, tracked and untracked) |

All routes use `Bearer <JWT>` authentication. The JWT is issued at login and stored in `localStorage` as `ft_jwt`.

---

## Admin Hub Pages

The hub has four pages accessible from the left sidebar.

### Overview (default landing page)
- 4 hero KPIs with delta trend arrows vs the prior 7-day window: Active Users (7d), Events (7d), Avg Score, Total Users
- Daily event sparkline (30d) with Y-axis labels, X-axis date ticks, and hover tooltip
- Quick-link cards to Users and Usage pages
- Source: `/admin/platform-stats` (current dataset only)

### Users
- Client-side search bar — filters teachers and students by name, email, or access code
- Teachers listed in an expandable accordion; each row shows student count, status badge, Activate/Deactivate button, and a link to their analytics dashboard
- Students listed per teacher (in accordion) and in a separate Solo Learners table
- Each user row has an **Activate / Deactivate** toggle wired to `PUT /admin/users/{id}` — changes take effect immediately and the list refreshes
- **Add Teacher** button (top-right) opens a modal; success/failure shown via toast
- Source: `/admin/user-hierarchy`

### Usage
- One KPI card per exercise, ordered by most used (30-day window)
- Each card shows: attempts (30d), unique users (30d), avg score badge (colour-coded green/amber/red), relative usage bar
- Exercises with no event tracking yet are shown dimmed with "not yet tracked"
- Full breakdown table below cards includes all-time totals
- Source: `/admin/feature-usage`

#### Exercise registry (in `analytics.get_feature_usage()`)

| Event type | Display name | Tracked |
|---|---|---|
| `phrase_attempted` | Shadowing | Yes |
| `paragraph_attempted` | Speaking — Passage | Yes |
| `paragraph_drilled` | Speaking — Sentence Drill | Yes |
| `word_attempted` | Practice List | Yes |
| — | Listen & Answer | No |
| — | Dictation | No |
| — | Flashcards | No |
| — | Prompted Writing | No |
| — | Transformation | No |
| — | My Content | No |

To add tracking for an untracked exercise: add the `_analytics.track()` call in the relevant server route, then update the `event_type` from `None` to the new event type string in the `EXERCISES` list inside `get_feature_usage()`.

### Performance
- Full KPI strip: Total Users, Teachers, Students, Total Events, Events (7d) with delta, Active Users (7d) with delta, Avg Score
- Daily event sparkline (30d) with axis labels and hover tooltip
- All-time exercise breakdown bar chart
- Top 10 active users table (30d) with links to their analytics dashboards
- **Current / Legacy dataset toggle** in the page header (see below)
- Source: `/admin/platform-stats?dataset=`

#### Dataset Toggle (Current / Legacy)
- **Current** — reads from `analytics.db` (live data from current deployment onward)
- **Legacy** — reads from `analytics_legacy.db` (data collected before the clean break)

The toggle passes `?dataset=legacy` to the backend. The backend uses a Python `ContextVar` (`analytics._active_db`) to switch the SQLite connection for the duration of that request without affecting concurrent requests.

A banner is shown when viewing legacy data: `VIEWING LEGACY DATA — collected before current deployment`.

Legacy DB path: `$DATA_DIR/analytics_legacy.db`  
Current DB path: `$DATA_DIR/analytics.db`

---

## platform-stats response shape

`GET /admin/platform-stats` returns:

```json
{
  "user_counts": { "teacher": 2, "student_teacher": 5, "student_solo": 1 },
  "total_users": 8,
  "total_events": 1234,
  "events_7d": 80,
  "events_30d": 310,
  "events_prev_7d": 65,
  "active_users_7d": 4,
  "active_users_prev_7d": 3,
  "avg_score": 0.712,
  "avg_score_prev_7d": 0.698,
  "exercise_breakdown": [{ "type": "paragraph_drilled", "count": 968 }, ...],
  "top_active_users": [{ "access_code": "abc", "events_30d": 120, "email": "...", "username": "..." }, ...],
  "daily_events": [{ "day": "2026-05-17", "count": 12 }, ...]
}
```

`events_prev_7d` and `active_users_prev_7d` cover the window 14–7 days ago and are used by the frontend to compute delta trend arrows.

---

## Data Persistence

Analytics data is stored in SQLite at `$DATA_DIR/analytics.db`. The `DATA_DIR` env var must point to a Render persistent disk mount, otherwise the DB is wiped on every deploy.

| Env Var | Value (Render) |
|---|---|
| `DATA_DIR` | `/opt/render/project/src/uploads/data` |

The legacy snapshot lives alongside the live DB at the same path as `analytics_legacy.db`. It was created manually via `cp analytics.db analytics_legacy.db` in the Render shell before the clean break.

---

## Creating a Teacher Account

From the admin hub: Users page → **Add Teacher** button (top-right). On success a toast confirms creation and the user list refreshes.

Or directly via the API:

```bash
curl -X POST https://your-app.onrender.com/admin/teachers \
  -H "Authorization: Bearer <super_admin_jwt>" \
  -H "Content-Type: application/json" \
  -d '{"email": "teacher@example.com", "password": "securepassword"}'
```

Teachers created this way have `force_pw_change = 0` and can log in immediately.

---

## Activating / Deactivating a User

In the Users page, each teacher row and each student row has an **Activate / Deactivate** button. Clicking it calls `PUT /admin/users/{id}` with `{ "is_active": 0 or 1 }`. The list refreshes automatically and a toast confirms the change.

Deactivated users cannot log in (auth middleware checks `is_active`).

---

## Registration Gate

Public registration at `/login` (Register tab) requires a valid **registration code** — one of the comma-separated values in the `ACCESS_CODES` env var on Render. Anyone without a valid code gets a `403` response.

Invite-based registration (teacher invites a student) bypasses the registration code check — the invite token is sufficient.

---

## Forgot Password Flow

1. User clicks "Forgot your password?" on `/login`
2. Enters email → `POST /auth/forgot-password`
3. Server generates a `secrets.token_urlsafe(32)` token, stores it in `password_reset_tokens` table (expires 1 hour, one-time use), sends reset link via Resend
4. Reset link: `/login?reset=<token>` — opens login page showing only the set-new-password form
5. User sets new password → `POST /auth/reset-password` — token consumed, password updated

**Rate limit:** 5 reset emails per email address per hour. Excess requests return `{"ok": true}` silently (no signal to attackers).

**Email sender:** configured via `EMAIL_FROM` env var (default: `VraiFrench <noreply@vraifrench.com>`). Sent via the SMTP2GO HTTP API; requires `SMTP2GO_API_KEY` to be set.

---

## Open Tool Link

The admin sidebar has an **↗ Open Tool** link that opens the main French practice app (`/`) in a new tab. The super admin account can use the tool for personal practice — exercises track events under the super admin's access code like any other user.
