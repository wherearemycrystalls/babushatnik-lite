#!/usr/bin/env bash
# Create an atomic, compressed PostgreSQL dump before every schema migration.
set -Eeuo pipefail
IFS=$'\n\t'

: "${DEPLOY_PATH:?DEPLOY_PATH is required}"

cd "$DEPLOY_PATH"
export APP_ENV_FILE=.env
compose=(docker compose --env-file .env -f docker-compose.prod.yml)

backup_dir="${BACKUP_DIR:-$DEPLOY_PATH/backups}"
retention_days="${BACKUP_RETENTION_DAYS:-$("${compose[@]}" config --environment | sed -n 's/^BACKUP_RETENTION_DAYS=//p')}"
retention_days="${retention_days:-14}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
tmp_file="$backup_dir/.kufar-$timestamp.sql.gz.tmp"
backup_file="$backup_dir/kufar-$timestamp.sql.gz"

[[ "$retention_days" =~ ^[0-9]+$ ]] || { echo "BACKUP_RETENTION_DAYS must be a non-negative integer" >&2; exit 2; }
umask 077
mkdir -p "$backup_dir"
trap 'rm -f "$tmp_file"' EXIT

# Expand PostgreSQL variables inside the container, not in the host shell.
# shellcheck disable=SC2016
"${compose[@]}" exec -T db \
  sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=plain --no-owner --no-privileges' \
  | gzip -9 > "$tmp_file"

[[ -s "$tmp_file" ]] || { echo "Database dump is empty" >&2; rm -f "$tmp_file"; exit 1; }
mv "$tmp_file" "$backup_file"
find "$backup_dir" -maxdepth 1 -type f -name 'kufar-*.sql.gz' -mtime "+$retention_days" -delete
echo "Database backup created: $backup_file"
