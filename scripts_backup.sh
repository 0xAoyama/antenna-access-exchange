#!/usr/bin/env bash
set -euo pipefail

mkdir -p backups
TS=$(date +%Y%m%d_%H%M%S)
OUT="backups/aae_${TS}.sql"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is required"
  exit 1
fi

pg_dump "$DATABASE_URL" > "$OUT"
echo "$OUT"
