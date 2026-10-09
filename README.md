# FreelanceFlow (through Week 6)

Multi-tenant Django backend: schema-per-tenant (django-tenants), subdomain routing, JWT auth,
role-based access, team invites, a CRM (clients, contacts, notes, tags, lead pipeline, timeline),
invoicing (line items, GST, numbering, payments, PDF), Stripe subscription billing with plan limits, a client portal, emailed invoices, payment reminders and recurring invoices, Postgres, Redis, Celery.

## Run
```bash
cp .env.example .env
docker compose up -d --build
docker compose exec web python manage.py makemigrations accounts tenants business crm invoicing billing
docker compose exec web python manage.py migrate_schemas
docker compose exec web python manage.py bootstrap_public
docker compose exec web python manage.py seed_demo --subdomain acme       # optional sample CRM data
docker compose exec web python manage.py seed_invoices --subdomain acme   # optional sample invoices (needs seed_demo first)
```
Tests: `docker compose exec web python manage.py test tests`

## Roles
owner > admin > accountant > viewer. Everyone reads. Writing CRM data needs owner/admin/accountant.
Deleting clients/leads/tags/contacts needs owner/admin. Team, invites and business profile: owner/admin.

## CRM endpoints (tenant domains, JWT required)
| Endpoint | Notes |
|---|---|
| `GET/POST /api/clients/` | `?q=` search, `?status=active\|archived\|all` (default active), `?tag=`, `?ordering=`, `?page=&page_size=` |
| `GET/PATCH/DELETE /api/clients/<id>/` | status changes only via the two actions below |
| `POST /api/clients/<id>/archive/`, `/restore/` | logged on the timeline |
| `GET /api/clients/<id>/timeline/` | paginated, newest first (includes pre-conversion lead history) |
| `GET/POST /api/clients/<id>/contacts/` , `PATCH/DELETE .../<cid>/` | one primary contact per client |
| `GET/POST /api/clients/<id>/notes/`, `DELETE .../<nid>/` | author or manager can delete |
| `GET/POST /api/tags/`, `PATCH/DELETE /api/tags/<id>/` | assign with `tag_ids` on a client |
| `GET/POST /api/leads/` | `?stage=lead\|contacted\|proposal\|won\|lost\|open`, `?q=`, `?ordering=-value_minor` |
| `GET /api/leads/board/` | Kanban: every stage with count, total value and its leads |
| `POST /api/leads/<id>/move/` `{"stage": "proposal"}` | stamps `closed_at` for won/lost, logs the move |
| `POST /api/leads/<id>/convert/` (optional `{"client_id": N}`) | creates a client + primary contact, marks the lead won |
| `GET /api/crm/stats/` | active/archived clients, pipeline value, conversion rate |

Money is an integer in minor units (`value_minor`: 5000000 = 50,000.00 INR).

## Invoicing endpoints (tenant domains, JWT required)
Everyone reads. Owner/admin/accountant write. Only owner/admin delete or void.

| Endpoint | Notes |
|---|---|
| `GET/POST /api/tax-rates/`, `PATCH/DELETE .../<id>/` | GST 0/5/12/18/28% are created on first use. One default. `rate_bps`: 1800 = 18% |
| `GET/POST /api/invoices/` | `?q=` (number, client, notes), `?status=draft\|sent\|partial\|paid\|void\|overdue\|open`, `?client=<id>`, `?issued_from=&issued_to=`, `?ordering=-due_date` |
| `GET/PATCH/DELETE /api/invoices/<id>/` | PATCH and DELETE only while the invoice is a draft. `lines` replaces all lines |
| `POST /api/invoices/<id>/send/` | draft -> sent: assigns `INV-0001`, sets dates (due = issue + client's payment terms), freezes seller/buyer details. Optional body `{"issue_date", "due_date"}` |
| `POST /api/invoices/<id>/void/` | owner/admin. Sent invoice with no payments. Number is kept, never reused. Optional `{"reason"}` |
| `GET/POST /api/invoices/<id>/payments/`, `DELETE .../<pid>/` | Records a payment and moves the invoice to partial or paid. Deleting one (owner/admin) moves it back |
| `GET /api/invoices/<id>/pdf/` | The invoice as a PDF (drafts are stamped DRAFT, void ones VOID) |
| `GET /api/invoices/summary/` | Per currency: outstanding, overdue, paid this month. Plus draft count |

Request body for an invoice:
```json
{"client": 1, "currency": "INR", "due_date": "2026-11-15", "notes": "Thank you",
 "lines": [{"description": "Website design", "quantity": 1, "unit_price_minor": 12000000, "tax_rate_id": 4}]}
```
Totals are always computed by the server. A foreign `currency` needs an `exchange_rate`.

## How invoicing works
- **Status flow**: `draft -> sent -> partial -> paid`, plus `void`. **Overdue is not stored**: it is a sent/partial invoice
  past its due date (`display_status: "overdue"`, `?status=overdue`), so it can never be stale. Week 6's nightly job
  only needs to send reminders.
- **Numbering**: assigned when sending, from a row-locked counter per prefix (`business-profile.invoice_prefix`).
  Numbers are consecutive; deleting drafts or voiding never creates gaps or reuse. Changing the prefix starts a new sequence.
- **Money**: integers in minor units. Quantity is a decimal. Tax is in basis points, rounded half-up once per line;
  invoice totals are the sum of the lines, so the PDF always adds up.
- **GST**: when seller and buyer states match, tax is shown as CGST + SGST halves, if they differ as IGST, if either is
  unknown as a plain "Tax" line. Decided at send time and frozen. Only for INR invoices.
- **Frozen details**: sending copies seller (business profile) and buyer (client) details onto the invoice, so editing a
  client later never rewrites an invoice that was already sent.
- **Clients with invoices** cannot be deleted (409): archive them.

## Known limits (on purpose, for now)
- PDF uses a built-in font: no Hindi/Tamil/other scripts and no rupee sign (money prints as `INR 1,000.00`).
- Money formatting assumes 2 decimal places (fine for INR/USD/EUR/GBP, not JPY).

## Billing (Stripe) — Week 5
Every organization has a subscription: **Free** until it pays. Plans and their limits are rows in the database
(`Plan.limits`, `Plan.features`), so changing a limit never needs a deploy. `null` means unlimited.

| Plan | Active clients | Invoices sent / month | Team members | Features |
|---|---|---|---|---|
| Free | 5 | 10 | 5 | |
| Pro (14-day trial) | 50 | 200 | 15 | recurring invoices, client portal |
| Business (14-day trial) | unlimited | unlimited | unlimited | + reports, audit log |

Edit them in the admin (`/admin/` on the bare domain) or the `billing_plan` table. Prices shown by the API are for display:
the real price lives in the Stripe Price you create.

### Setup (test mode, no real money)
1. Stripe dashboard (Test mode) -> Product catalog: create **FreelanceFlow Pro** and **FreelanceFlow Business** as recurring monthly prices. Copy each `price_...` id.
2. Settings -> Billing -> Customer portal: turn it on and allow cancelling subscriptions.
3. Developers -> API keys: copy the secret key `sk_test_...`.
4. Install the Stripe CLI, run `stripe login`, then `stripe listen --forward-to localhost:8000/api/billing/webhook/`. It prints a signing secret `whsec_...`.
5. Add to `.env` (never commit it): `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_PRO`, `STRIPE_PRICE_BUSINESS`. Optional: `BILLING_GRACE_DAYS` (default 7).
6. `docker compose up -d` (picks up `.env` through `docker-compose.override.yml`), then `docker compose exec web python manage.py seed_plans` shows what is configured.

For a deployed server, create a webhook endpoint in the dashboard pointing at `https://<your bare domain>/api/billing/webhook/` and subscribe it to:
`checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`,
`customer.subscription.deleted`, `customer.subscription.trial_will_end`, `invoice.paid`, `invoice.payment_failed`.

### Endpoints
| Endpoint | Who | Notes |
|---|---|---|
| `GET /api/billing/plans/` | any member | the plan catalog |
| `GET /api/billing/subscription/` | any member | plan, status, trial/period dates, grace period, usage vs limits, features |
| `POST /api/billing/checkout/` `{"plan": "pro"}` | owner | returns `{"url"}`: redirect the browser there. First checkout gets the trial |
| `POST /api/billing/portal/` | owner | returns `{"url"}` of the Stripe Customer Portal (cards, invoices, cancel) |
| `POST /api/billing/change-plan/` `{"plan": "business"}` | owner | upgrade/downgrade between paid plans with proration. Returns 202; applied when Stripe confirms |
| `POST /api/billing/webhook/` | Stripe | on the **bare domain** only. Signature-verified, no login |

### Rules worth knowing
- **Webhooks are the source of truth.** Only `webhooks.py` changes a plan or status. Checkout returning "success" changes nothing:
  the frontend polls `GET /api/billing/subscription/` until the plan changes.
- **Idempotent**: each Stripe event id is stored in the same transaction as its effects, so a retried event is acknowledged and
  ignored. If processing fails, nothing is kept and Stripe retries later. Events older than the newest applied one are ignored.
- **Failed payment**: status `past_due`, and the paid plan keeps working for `BILLING_GRACE_DAYS` (Stripe retries the card meanwhile).
  After that, access drops to Free **automatically** (computed on read, no scheduler needed). A successful payment clears it.
  Week 6 adds the reminder emails.
- **Limits stop new things only**: creating or restoring a client, **sending** an invoice (drafts are free), inviting a teammate.
  They answer HTTP 402 `{"code": "plan_limit_reached", "limit_key", "limit", "used", "plan"}` so the UI can show an upgrade prompt.
  Downgrading never deletes or hides existing data.
- **Feature flags**: `permission_classes = [TenantRolePermission, requires_feature("recurring_invoices")]` answers 402 `feature_not_available`.
- One free trial per organization (`trial_used`).
- Stripe's API changed shape in 2025 (`current_period_end` moved onto subscription items). The webhook code reads both shapes.

## Known limits (Week 5)
- Free-plan tenants cannot cancel or change anything: there is nothing to change. Cancelling a paid plan is done in the Customer Portal.
- Plan limits are checked just before the action, not under a lock: two simultaneous requests could exceed a limit by one.
- Team limit counts members plus pending invitations; accepting an invite never re-checks the limit.
- Real-money payments in India have extra Stripe requirements. This week is built and tested for Stripe **test mode**.

## Email, portal, reminders, recurring invoices, timezone — Week 6

### How the background work runs
Slow or scheduled work runs in Celery, never inside a web request. The `worker` container runs the tasks and, in development,
the scheduler too (`celery -A config worker -B`, set in `docker-compose.override.yml`). Tasks get plain values (schema name,
ids), switch to the organization's schema, and do the work. In development emails print in the **worker** logs
(`docker compose logs worker`), because that is where they are sent from. Tests run tasks inline (`CELERY_TASK_ALWAYS_EAGER`).

Once an hour the scheduler runs `invoicing.run_scheduled_jobs`. For each organization it runs the daily jobs **once per local day,
at the first run after 09:00 in that organization's timezone**: first recurring invoices, then payment reminders. Both are
idempotent, so a crash halfway just retries at the next tick. To run them by hand:
`docker compose exec web python manage.py run_scheduled --subdomain acme --force`

### Timezone and settings: `GET/PATCH /api/invoicing/settings/`
| Field | Default | Meaning |
|---|---|---|
| `timezone` | `UTC` | e.g. `Asia/Kolkata`. Decides what "today" is for due dates, overdue, reminders and the monthly invoice limit |
| `reminders_enabled` | true | master switch for payment reminders |
| `reminder_offsets` | `[-3, 1, 7, 14]` | days relative to the due date (negative = before). At most 6, each between -30 and 120 |
| `email_signature` | empty | closes every email |

### Emailing an invoice
- `POST /api/invoices/<id>/send/` with `{"email": true}` also emails the PDF to the client. Without the flag it only changes the status.
- `POST /api/invoices/<id>/email/` (optional `{"message": "..."}`) re-sends it. Only sent invoices with a balance can be emailed.
- Recipient: the primary contact's email, else any contact with an email, else the client's email. With none, the API says so (nothing fails).
- The email contains the portal link when the plan includes `client_portal`.
- Real email: set `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`,
  `EMAIL_HOST_PASSWORD` in `.env`.

### Client portal (Pro and Business)
- `GET /api/invoices/<id>/portal-link/` returns `{"enabled", "url"}`. `POST` replaces the link (the old one stops working).
- `GET /portal/<token>/` is a public page (no login) with a Download PDF button. `/portal/<token>/pdf/` is the PDF and
  `/api/portal/<token>/` is the JSON twin for a future React portal.
- The token is 192 random bits. Unknown, draft or wrong-organization tokens all answer 404. The page works only while the plan
  includes the portal. Responses are `no-store` and `noindex`, rate limited to 60/min per IP, and all client text is HTML-escaped.
- The first view is recorded (`viewed_at`, plus a timeline entry).

### Payment reminders (all plans)
- At most **one reminder per invoice per day**, the most recent applicable one. Older offsets that were never sent are recorded as
  skipped, so a late invoice gets one email, not a burst.
- A reminder is claimed (unique per invoice and offset) before it is queued, so a repeated run can never double-send.
- Never on the day the invoice itself was sent. Paid and void invoices are left alone. Wording follows reality: "is due in 3 days",
  "is overdue by 7 days".

### Recurring invoices (Pro and Business): `/api/recurring-invoices/`
- Create with a client, `frequency` (`weekly`, `monthly`, `quarterly`, `yearly`), `interval`, `start_date`, `lines`, optional
  `end_date`, `max_runs`, `auto_send`. The first run cannot be in the past.
- Each run creates a **draft** (or sends and emails it with `auto_send`). If auto-send is blocked (plan limit), the draft stays
  and `last_error` says why. If the client was archived the schedule pauses with `last_error`.
- Dates are counted from the start date, so "monthly from the 31st" gives Feb 28, Mar 31, Apr 30 and never drifts.
- The schedule advances in the same transaction that creates the invoice: a crash can neither skip a period nor bill it twice.
  A missed schedule catches up one invoice per day, never a flood.
- `pause`, `resume` (after a long pause it continues from today instead of back-dating), `run-now`. The schedule (frequency,
  interval, start date) is locked once an invoice has been created. Deleting a schedule keeps the invoices it made.

### Billing emails
The owner gets one email when a payment first fails (not one per card retry) and one before a trial ends
(`customer.subscription.trial_will_end`). Emails are queued only after the database transaction commits.

## Known limits (Week 6)
- Emails are plain text with the PDF attached. The PDF still uses a built-in font (no Hindi/Tamil, no rupee sign).
- Reminders and the daily jobs run at most hourly; "9am" is approximate to the hour.
- Reminder claiming is at-most-once: if the mail server is down for longer than the retries (3), that reminder is not re-sent.
- The portal is read-only: there is no online payment button yet (Stripe Connect is the Week 8 stretch).
- In development the scheduler runs inside the worker. In production run `celery -A config beat` as its own single process.
