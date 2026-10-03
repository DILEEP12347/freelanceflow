# FreelanceFlow (Week 1)

Multi-tenant Django skeleton: schema-per-tenant (django-tenants), subdomain routing, Postgres, Redis, Celery.

## Run
```bash
cp .env.example .env
docker compose up -d --build
docker compose exec web python manage.py makemigrations accounts tenants crm
docker compose exec web python manage.py migrate_schemas --shared
docker compose exec web python manage.py bootstrap_public
docker compose exec web python manage.py createsuperuser

# create a tenant via API
curl -X POST http://localhost:8000/api/tenants/signup/ \
  -H "Content-Type: application/json" \
  -d '{"name":"Acme Studio","subdomain":"acme"}'

# hit the tenant
curl http://acme.localhost:8000/api/health/
curl -X POST http://acme.localhost:8000/api/clients/ -H "Content-Type: application/json" -d '{"name":"First client"}'
curl http://acme.localhost:8000/api/clients/
```
Run tests: `docker compose exec web python manage.py test tests`
