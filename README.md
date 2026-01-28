# Credit Approval System (Django + DRF + Celery)

## Stack
- Django 4.2, Django REST Framework
- Celery + Redis (broker/result) for background ingestion
- PostgreSQL (primary DB)
- Docker Compose for web, worker, db, redis

## Prerequisites
- Docker + Docker Compose
- `data/customer_data.xlsx` and `data/loan_data.xlsx` placed in the repo `data/` folder

## Quick Start
```bash
cp .env.example .env
docker-compose up -d --build
```
- Web API: http://localhost:8000/
- Redis: 6379, Postgres: 5432 (mapped to host)
- Worker logs: `docker-compose logs -f worker`

## Ingestion
Enqueue background ingestion of the Excel files:
```bash
docker-compose exec web python manage.py ingest_initial_data
```
Check worker logs for created/skipped counts:
```bash
docker-compose logs -f worker
```
Files are read from `./data` (mounted to `/app/data`).

## API Endpoints (prefix `/credit/`)
- `POST /credit/register/`  
  Request: `first_name`, `last_name`, `age` (optional), `monthly_income`, `phone_number`  
  Response: `customer_id`, `name`, `age`, `monthly_income`, `approved_limit`, `phone_number`

- `POST /credit/check-eligibility/`  
  Request: `customer_id`, `loan_amount`, `interest_rate`, `tenure`  
  Response: approval decision, corrected interest if needed, monthly_installment

- `POST /credit/create-loan/`  
  Request: `customer_id`, `loan_amount`, `interest_rate`, `tenure`  
  Response: `loan_id`, `loan_approved` flag, message, `monthly_installment`

- `GET /credit/view-loan/<loan_id>/`  
  Response: loan details + customer summary

- `GET /credit/view-loans/<customer_id>/`  
  Response: list of loans with `loan_id`, `loan_amount`, `interest_rate`, `monthly_installment`, `repayments_left`

## Business Logic Highlights
- Approved limit on register: `36 * monthly_income`, rounded to nearest lakh
- Eligibility slabs: score-based; low scores denied; EMI cap at 50% of monthly salary
- Ingestion: Celery tasks load Excel via pandas, bulk_create with `ignore_conflicts`, idempotent, skips missing customers and logs issues

## Development Notes
- Env-driven settings: `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `POSTGRES_*`, optional `REDIS_URL`, `DATA_DIR`
- Migrations run on web startup; for clean reload, `docker-compose down -v` then `up --build`
- To inspect data: `docker-compose exec web python manage.py shell -c "from credit.models import Customer, Loan; print(Customer.objects.count(), Loan.objects.count())"`

