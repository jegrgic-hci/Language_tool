# Account & Authentication System

## Overview

Role-based user account system replacing the legacy hardcoded access-code flow. Users log in at `/login`; tokens are stored in `localStorage` and passed as `Authorization: Bearer <token>` headers.

---

## Roles

| Role | Description |
|---|---|
| `super_admin` | Full access to everything — admin API, all teacher dashboards, all students |
| `teacher` | Access to their own students only; teacher dashboard scoped to their roster |
| `student_teacher` | Added by a teacher; receives auto-generated credentials |
| `student_solo` | Self-registers via the Register tab on the login page |

---

## Auth Flow

### Token model
- **Access token** — short-lived JWT (60 min), stored as `ft_jwt` in localStorage
- **Refresh token** — 7-day token, stored as `ft_refresh_token` in localStorage
- **Access code** — 8-char hex string embedded in the JWT and stored as `ft_access_code`; used by exercise endpoints unchanged (no auth header required on `/track`, `/shadow/*`, `/paragraph/*`, etc.)

### Login flow
1. User visits `/` (landing page) → clicks Sign In → `/login`
2. Submit email + password → `POST /auth/login`
3. Tokens + access_code stored in localStorage
4. If `force_pw_change: true` → inline password-change form appears before redirecting
5. Redirect by role: `super_admin` → `/admin`; `teacher` → `/analytics/dashboard`; student → `/app`

The already-logged-in check on `login.html` applies the same role split on page load.

### Forgot password
No automated email delivery is implemented. The "Forgot your password?" link in `login.html` shows a contextual message:
- Teacher-added students: directed to ask their teacher to reset via the Manage Students tab
- Self-registered (`student_solo`): directed to email `jegrgic@gmail.com`

### Token refresh
`apiFetch()` (in `index.html`, `analytics.html`, and `admin.html`) intercepts `401` responses, calls `POST /auth/refresh`, retries the request automatically.

---

## Environment Variables

Add to `.env`:

```
JWT_SECRET=<64-char random hex — generate with: python -c "import secrets; print(secrets.token_hex(32))">
JWT_ACCESS_EXPIRE_MINUTES=60
JWT_REFRESH_EXPIRE_DAYS=7
SUPER_ADMIN_EMAIL=jegrgic@gmail.com
SUPER_ADMIN_PASSWORD=<your password>
```

**JWT_SECRET** — if not set, a random ephemeral secret is generated at startup. All tokens will be invalidated on every server restart. Always set this in production.

---

## Database Tables

### `users`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `role` | TEXT | `super_admin` / `teacher` / `student_teacher` / `student_solo` |
| `email` | TEXT UNIQUE | Login identifier |
| `username` | TEXT UNIQUE | Auto-generated for `student_teacher`; null for teacher/admin |
| `password_hash` | TEXT | bcrypt via passlib |
| `is_active` | INTEGER | 0 = soft-deactivated |
| `teacher_id` | INTEGER FK→users | Populated for `student_teacher` rows |
| `access_code` | TEXT UNIQUE | 8-char hex; bridges to `/track` and exercise scoring system |
| `force_pw_change` | INTEGER | 1 = student must change password on next login |
| `created_at` | DATETIME | Account creation date; shown as "Member since" in the student account view |
| `plan_name` | TEXT | e.g. `Monthly`, `Term` — nullable; set by teacher/admin |
| `plan_price` | TEXT | e.g. `€50/mo` — nullable; displayed alongside `plan_name` |
| `billing_date` | TEXT | ISO date string of next billing date — nullable |

`plan_name`, `plan_price`, and `billing_date` were added via `ALTER TABLE` migration in `_init_db()` and are safe to run on existing DBs (duplicate-column errors are silently ignored).

### `refresh_tokens`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `user_id` | INTEGER FK→users | |
| `token_hash` | TEXT UNIQUE | bcrypt hash of the raw token |
| `expires_at` | DATETIME | 7 days from creation |

---

## API Endpoints

### Auth (no authentication required unless noted)
| Method | Path | Body | Response |
|---|---|---|---|
| `POST` | `/auth/login` | `{email, password}` | `{access_token, refresh_token, role, access_code, force_pw_change}` |
| `POST` | `/auth/register` | `{email, password}` | Same as login — creates `student_solo` |
| `POST` | `/auth/refresh` | `{refresh_token}` | `{access_token}` |
| `POST` | `/auth/logout` | `{refresh_token}` | `{ok: true}` — invalidates the refresh token |
| `POST` | `/auth/change-password` | `{current_password, new_password}` | `{ok: true}` — **requires Bearer token** |
| `GET` | `/auth/me` | — | See below — **requires Bearer token** |

#### `GET /auth/me` response
```json
{
  "id": 1,
  "role": "student_teacher",
  "email": "student@example.com",
  "username": "abc12345",
  "access_code": "a1b2c3d4",
  "force_pw_change": false,
  "created_at": "2026-01-15 10:32:00",
  "plan_name": "Monthly",
  "plan_price": "€50/mo",
  "billing_date": "2026-07-15",
  "next_lesson": "2026-06-18"
}
```
`next_lesson` is computed from the `students.lesson_days` field for this student's `access_code`. All billing fields are `null` if unset.

### Teacher — student management (requires `teacher` or `super_admin` JWT)
| Method | Path | Notes |
|---|---|---|
| `GET` | `/teacher/students` | Returns students with `event_count` and `last_active` fields derived from the events table |
| `POST` | `/teacher/students` | `{email, name, lesson_days, lesson_time, notes}` → returns `{username, temp_password, access_code}` |
| `POST` | `/teacher/students/{user_id}/reset-password` | Returns new `{username, temp_password}`; sets `force_pw_change=1` |
| `PATCH` | `/teacher/students/{user_id}/status` | `{is_active: 0\|1}` — pause or reactivate a student |
| `DELETE` | `/teacher/students/{user_id}` | Soft-deactivates: sets `is_active=0`, revokes refresh tokens |

### Admin (requires `super_admin` JWT)
| Method | Path | Notes |
|---|---|---|
| `GET` | `/admin/users` | All users, all roles |
| `GET` | `/admin/teachers` | Teachers only |
| `POST` | `/admin/teachers` | `{email, password}` — creates teacher account |
| `PUT` | `/admin/users/{user_id}` | `{role?, is_active?}` — update role or reactivate |
| `GET` | `/admin/user-hierarchy` | Teachers with nested students + solo learners; annotated with event count and last active |
| `GET` | `/admin/platform-stats` | Platform-wide metrics: user counts by role, total/7d/30d events, avg score, exercise breakdown, top active users (30d), daily sparkline data |

---

## Files

| File | Role |
|---|---|
| `auth.py` | JWT creation/verification, bcrypt helpers, FastAPI `Depends()` guards, temp credential generation |
| `analytics.py` | `users` + `refresh_tokens` table DDL (incl. billing column migrations); all user/token DB helpers; `seed_super_admin()`; `get_platform_stats()`; `get_user_hierarchy()`; `get_students_for_teacher_user()` (includes `event_count` + `last_active`) |
| `server.py` | All auth/teacher/admin routes; `_require_analytics_key` shim (accepts legacy `?key=` OR Bearer JWT); page routes `/`, `/login`, `/app`, `/admin`, `/analytics/dashboard` |
| `static/login.html` | Login + register gate; force-password-change sub-form; forgot-password panel; role-based redirect. Served at `/login`. |
| `static/index.html` | Student tool; JWT redirect guard; `apiFetch()` helper; hash-based SPA routing (`/app#home`, `/app#phrase-hub`, etc.); account view (profile, membership info, password change); first-time onboarding modal for `student_solo` |
| `static/analytics.html` | Teacher performance dashboard; sidebar panels: roster (All Students), per-student analytics, Manage Students (roster table only), Add Student (dedicated form); served at `/analytics/dashboard` |
| `static/admin.html` | Super admin hub: user hierarchy, teacher management, platform stats; served at `/admin` |
| `static/landing.html` | Public marketing page; served at `/`; pricing section at `#pricing` |
| `static/dev.html` | Dev-only launcher; one-click login for each account type; served at `/dev` |

---

## Page Map

| URL | Who sees it | Purpose |
|---|---|---|
| `/` | Everyone (unauthenticated) | Public landing page; authenticated users are redirected by role on load |
| `/login` | Everyone | Login + self-register |
| `/app` | Students | French learning tool; hash-routed (`#home`, `#phrase-hub`, `#account`, etc.) |
| `/app#account` | Students | Account view: profile, membership & billing info, password change, sign out |
| `/analytics/dashboard` | Teachers, super_admin | Performance metrics + student roster management; profile panel via sidebar avatar |
| `/admin` | super_admin only | Platform-wide admin hub |
| `/dev` | Local development only | One-click login launcher for all account types |

---

## Sidebar Profile Block (Student & Teacher)

Both the student tool and teacher dashboard show a profile block pinned to the bottom of the sidebar, using the `vk-sb-profile` component from `vk-components.css`.

**Student (`index.html`)**
- Avatar (person SVG icon in accent circle) + name/email + role label
- Clicking the avatar/name area opens the account view (`switchView('account')`) — active state highlights the avatar
- "Sign out" button clears `ft_jwt`, `ft_refresh_token`, `ft_access_code` from localStorage and redirects to `/login`
- Collapsed sidebar: profile info and sign-out text hide; only the avatar icon remains, centred

**Teacher (`analytics.html`)**
- Same component; name populated via `GET /auth/me` on load (`_profileData` cached globally)
- Clicking the avatar/name area opens `panel-profile` via `selectPanel('profile', this)` — `id="nav-profile"` participates in the standard `allNavBtns` active-state system
- "Sign out" remains a separate `vk-sb-signout` button beside the clickable area
- No collapsed state on the teacher sidebar

**Component classes** (defined in `vk-components.css`):
- `.vk-sb-profile` — outer flex row with border-top
- `.vk-sb-profile-btn` — clickable button wrapping avatar + info; carries `data-view` for active-state toggling
- `.vk-sb-avatar` — 28px accent circle holding the SVG icon
- `.vk-sb-profile-info` — name + role label stack
- `.vk-sb-profile-name` / `.vk-sb-profile-role` — text elements
- `.vk-sb-signout` — muted button; turns `--vk-error` on hover

---

## Student Account View (`/app#account`)

Opened by clicking the sidebar profile block. Sections:

### Account
- Email address and role label (Independent learner / Student)

### Membership
2-column grid showing four fields populated from `GET /auth/me`:

| Field | Source |
|---|---|
| Member since | `users.created_at` — formatted as "16 June 2026" |
| Plan | `users.plan_name` + `users.plan_price` — e.g. "Monthly · €50/mo"; shows "—" if unset |
| Next billing | `users.billing_date` — ISO date, formatted |
| Next lesson | Computed from `students.lesson_days` for this student's access code |

Billing fields (`plan_name`, `plan_price`, `billing_date`) are set by the teacher/admin directly in the DB or via a future Manage Students field. The model is undecided — fields exist as nullable strings with no payment processor integration yet.

### Change Password
Calls `POST /auth/change-password`; validates match and minimum 8 characters; shows inline success/error.

### Session
Full-width "Sign out" button (destructive style).

---

## Teacher Profile View (`/analytics/dashboard` → profile panel)

Opened by clicking the sidebar avatar/name block. Panel id: `panel-profile`. Sections:

### Profile
2-column info grid populated from `_profileData` (cached from `GET /auth/me` on page load):

| Field | Source |
|---|---|
| Email | `users.email` |
| Role | Static label "Teacher" |
| Member since | `users.created_at` — formatted as "16 June 2026" |

### Roster (last 7 days)
Three KPI cards computed from `window._rosterStudents` (already loaded at init — no extra request):

| KPI | Computation |
|---|---|
| Students | `students.length` |
| Sessions | Sum of `s.sessions_7d` across all students |
| Avg Accuracy | Mean of `s.avg_score_7d` for students with a score; shown as `%` |

### Change Password
Calls `POST /auth/change-password` via `_mgmtFetch` (handles JWT + refresh); validates match and minimum 8 characters; shows inline success/error.

### Session
"Sign out" button (destructive style) — clears tokens and redirects to `/login`.

---

## Existing Data (Legacy Silo)

The legacy `events`, `students`, `teachers`, and `coach_cache` tables are untouched. Old access-code-keyed data remains queryable but is not linked to any `users` row. The `ANALYTICS_KEY` env var still works as a backwards-compatible auth path on all `/analytics/*` endpoints.

---

## Teacher Workflow

1. Log in at `/login` → land on `/analytics/dashboard`
2. Sidebar shows **Go to Tool** (accent button) at the top and profile/sign-out at the bottom
3. **Add student**: click **+ Add Student** in the Admin section of the sidebar → dedicated form with name, email, lesson days (Mon–Sun chip toggles), lesson time, and notes → submit → credential modal shows email, username, and temp password with per-field and bulk copy buttons; shown once only
4. Student logs in at `/login` with temp credentials → prompted to set their own password → lands on `/app`
5. **Manage Students**: click **Manage Students** in the sidebar → roster table only (no form)
6. **Reset password**: click Reset Password on the student row → confirm → new credentials shown in modal
7. **Pause / Reactivate**: toggles `is_active`; paused students cannot log in but data is preserved
8. **Remove**: soft-deactivates the account; data preserved in DB
9. **Activity at a glance**: roster table shows Last Active date and total session count per student

---

## Super Admin Workflow

1. Log in at `/login` → land on `/admin`
2. **Users tab**: see all teachers (expandable to show their students) + solo learners; open any student's Analytics or Open Tool link; add a new teacher via modal
3. **Performance tab**: platform-wide KPIs, 30-day event sparkline, exercise type breakdown, top active users

---

## Student Onboarding

First-time `student_solo` users see a welcome modal on their first visit to `/app`. It explains the listen → speak → improve loop, recommends starting with Phrase Shadowing, and notes the Chrome/Edge mic requirement. Dismissed by clicking "Start practising", which sets `localStorage.vf_onboarded = '1'`. The modal does not appear for `student_teacher` accounts or on subsequent visits.

---

## Pricing

The landing page includes a 3-tier pricing section (`#pricing`): **Free** (solo learner, self-register), **Pro** (teacher, by request), **Custom** (school/institution, contact). Teacher accounts cannot be self-created — the Pro CTA links to a mailto. To hide the pricing section until ready, add `style="display:none"` to `<section id="pricing">` in `landing.html`.

Planned subscription integration:
- Billing model is undecided; `plan_name`, `plan_price`, and `billing_date` on the `users` table act as a lightweight placeholder
- Future options: Stripe subscription with a `subscriptions` table, or a simple `plan TEXT` column for manual gating
- Add the `plan` claim to the JWT payload once decided so endpoints can gate features without an extra DB lookup
- Solo students are the natural paid tier; teacher accounts suit per-seat or school plans
