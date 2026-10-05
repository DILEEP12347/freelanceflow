# FreelanceFlow (through Week 3)

Multi-tenant Django backend: schema-per-tenant (django-tenants), subdomain routing, JWT auth,
role-based access, team invites, a CRM (clients, contacts, notes, tags, lead pipeline, timeline),
Postgres, Redis, Celery.

## Run
```bash
cp .env.example .env
docker compose up -d --build
docker compose exec web python manage.py makemigrations accounts tenants business crm
docker compose exec web python manage.py migrate_schemas
docker compose exec web python manage.py bootstrap_public
docker compose exec web python manage.py seed_demo --subdomain acme   # optional sample data
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
