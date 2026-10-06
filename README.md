# FreelanceFlow (through Week 4)

Multi-tenant Django backend: schema-per-tenant (django-tenants), subdomain routing, JWT auth,
role-based access, team invites, a CRM (clients, contacts, notes, tags, lead pipeline, timeline),
invoicing (line items, GST, numbering, payments, PDF), Postgres, Redis, Celery.

## Run
```bash
cp .env.example .env
docker compose up -d --build
docker compose exec web python manage.py makemigrations accounts tenants business crm invoicing
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
- Dates use the server's UTC day. A per-business timezone arrives with the Week 6 overdue job.
- Money formatting assumes 2 decimal places (fine for INR/USD/EUR/GBP, not JPY).
- Sending only changes the status. Week 6 adds the email with the PDF attached.
