# Production Runbook

## 1) Required env vars
AAE_SECRET_KEY=<long-random>
AAE_ADMIN_USER=<admin-user>
AAE_ADMIN_PASSWORD=<strong-password>
DATABASE_URL=postgresql://USER:PASS@HOST:5432/DBNAME
AAE_MAX_REQ_PER_MIN=120
AAE_CLICK_DEDUP_SEC=600

## 2) Start command
gunicorn -w 2 -k gthread --threads 4 --timeout 60 -b 0.0.0.0:${PORT:-8000} app:app

## 3) Health checks
GET /healthz
Expect: {"ok":true,...}

## 4) Backup / Restore
export DATABASE_URL=...
./scripts_backup.sh
./scripts_restore.sh backups/aae_YYYYmmdd_HHMMSS.sql

## 5) Security baseline
HTTPS termination required at LB/proxy
Rotate AAE_SECRET_KEY and admin password regularly
Restrict admin access by IP at ingress if possible

## 6) Incident response
If fraud suspected:
login /admin
ban target site
inspect /admin/audit logs
apply manual counter adjustment

## 7) Alerts (minimum)
HTTP 5xx rate > 1% for 5min
/healthz failure 3 consecutive checks
DB connection failures > 3/min
