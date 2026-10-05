# FreelanceFlow (through Week 2)

Multi-tenant Django backend: schema-per-tenant (django-tenants), subdomain routing, JWT auth,
role-based access, team invites, Postgres, Redis, Celery.

## Run
```bash
cp .env.example .env
docker compose up -d --build
docker compose exec web python manage.py makemigrations accounts tenants business
docker compose exec web python manage.py migrate_schemas
docker compose exec web python manage.py bootstrap_public
```
Tests: `docker compose exec web python manage.py test tests`

## Auth model
- **Identity**: email + password -> JWT (access 15 min, refresh 7 days). Users live in the public schema.
- **Access**: a user's `Membership` (role) in the tenant being requested. A token for tenant A is a 403 on tenant B.
- **Roles**: owner > admin > accountant > viewer.

| Resource | viewer | accountant | admin | owner |
|---|---|---|---|---|
| Clients (read / write) | R | R W | R W | R W |
| Business profile | R | R | R W | R W |
| Members (list / change / remove) | list | list | all but admins/owner | all but owner |
| Invites | - | - | create (not admin role) | create |

## Endpoints
Public domain (`localhost:8000`) and tenant domains (`acme.localhost:8000`):
`POST /api/auth/register/`, `GET|POST /api/auth/verify-email/`, `POST /api/auth/resend-verification/`,
`POST /api/auth/login/`, `POST /api/auth/refresh/`, `GET /api/auth/me/`

Public domain only: `POST /api/tenants/signup/` (login required; caller becomes Owner)

Tenant domains only: `/api/clients/`, `/api/business-profile/`, `GET|PATCH|DELETE /api/team/members/`,
`GET|POST|DELETE /api/team/invites/`, `POST /api/team/invites/accept/`

Emails (verification, invites) print in the `web` container logs: `docker compose logs web`.
