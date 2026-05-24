# Public Launch Plan — French Tutor Subscription Product

## Goal
Turn the French Tutor webapp into a public subscription product at $5/month, targeting US customers learning French. Free 7-day trial, then paid.

---

## Stack additions required
- **Auth**: fastapi-users or Supabase Auth
- **Payments**: Stripe (subscriptions + webhooks)
- **Database**: Render Postgres (replacing SQLite for user/subscription data)
- **Hosting**: Already on Render Starter ($7/mo)

---

## Phase 1 — Database migration
SQLite works for one user but fails under concurrent writes from multiple accounts.

- Add Render Postgres add-on ($7/mo, 1GB)
- Migrate existing analytics tables to Postgres
- Add `users` and `subscriptions` tables

### Schema (minimum viable)
```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    trial_ends_at TIMESTAMP,
    stripe_customer_id TEXT
);

CREATE TABLE subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    stripe_subscription_id TEXT,
    status TEXT,  -- active, trialing, canceled, past_due
    current_period_end TIMESTAMP,
    updated_at TIMESTAMP DEFAULT NOW()
);
```

---

## Phase 2 — Auth
Every `/chat` request must be authenticated and subscription-checked.

### Endpoints to add
| Endpoint | Method | Purpose |
|---|---|---|
| `/auth/signup` | POST | Create account, start 7-day trial |
| `/auth/login` | POST | Return JWT token |
| `/auth/me` | GET | Return current user + subscription status |
| `/auth/reset-password` | POST | Trigger password reset email |

### Middleware
- JWT validation on every `/chat`, `/audio`, `/upload` request
- Subscription check: `status IN ('trialing', 'active')` — if not, return 402
- Session ID currently stored in localStorage — tie it to user ID on login

### Free trial logic
- On signup: set `trial_ends_at = NOW() + 7 days`
- Subscription check: if `trial_ends_at > NOW()` AND no Stripe subscription → allow
- After trial: require active Stripe subscription

---

## Phase 3 — Stripe integration
Never touch card data directly — Stripe handles everything.

### Flow
1. User signs up → 7-day trial starts (no card required)
2. Day 5–6: prompt user to add payment method
3. User clicks "Subscribe" → create Stripe Checkout session → redirect to Stripe
4. Stripe handles payment → fires `customer.subscription.created` webhook
5. Webhook handler updates `subscriptions` table → user gains access

### Webhooks to handle
| Event | Action |
|---|---|
| `customer.subscription.created` | Set status = active |
| `customer.subscription.updated` | Update status + period_end |
| `customer.subscription.deleted` | Set status = canceled |
| `invoice.payment_failed` | Set status = past_due, email user |

### Usage cap (free trial abuse prevention)
- Limit free trial users to 50 messages/day
- Track in Postgres: `daily_message_counts(user_id, date, count)`
- Paid users: unlimited

---

## Phase 4 — Frontend changes
Minimal changes to existing `index.html`:

- **Login/signup screen**: shown if no valid JWT in localStorage
- **Trial banner**: "X days left in your trial — subscribe to keep access"
- **Paywall modal**: shown when trial expires or message limit hit
- **Account menu**: email, subscription status, cancel link (Stripe customer portal)

---

## Phase 5 — Admin
Simple protected route, no full dashboard needed at MVP.

### `/admin` endpoint (protected by env-var secret key)
- List of all users + signup date + subscription status
- Revenue summary (active subscribers × $5)
- Ability to manually extend a trial or cancel a subscription
- Link to full Stripe dashboard for billing details

---

## Cost model at scale

| Users | Revenue | Costs | Net |
|---|---|---|---|
| 1 | $0–5 | $7 | -$2 to +$0 |
| 10 | $50 | $20 | ~$30 |
| 50 | $200 | $30 | ~$170 |
| 200 | $1,000 | $80 | ~$920 |

Costs at 50 users: Render $7 + Postgres $7 + Mistral ~$10 + Azure TTS $5 + Stripe fees ~$10 = ~$39/mo

---

## Build order (recommended)
1. Postgres migration
2. Auth (signup, login, JWT middleware)
3. Free trial logic
4. Stripe checkout + webhooks
5. Frontend auth screens + trial banner
6. Usage cap
7. Admin route
8. Landing page (out of scope for MVP — can be a simple static page)

---

## Open questions
- Require credit card at signup or at end of trial?
  - Recommended: no card at signup, prompt on day 5. Lower friction, higher conversion.
- Password reset email — need a transactional email provider (Resend or Postmark, both have free tiers)
- Custom domain + HTTPS — Render supports this on paid plans
