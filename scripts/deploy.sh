#!/usr/bin/env bash
# Idempotently promote an already-published image on the production host.
set -Eeuo pipefail
IFS=$'\n\t'

: "${DEPLOY_PATH:?DEPLOY_PATH is required}"
: "${APP_IMAGE:?APP_IMAGE is required}"
: "${GHCR_USERNAME:?GHCR_USERNAME is required}"

[[ "$APP_IMAGE" =~ ^ghcr\.io/[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}$ ]] || { echo "APP_IMAGE must be a lower-case GHCR digest reference" >&2; exit 2; }
[[ -f "$DEPLOY_PATH/docker-compose.prod.yml" ]] || { echo "Missing production compose file" >&2; exit 2; }
[[ -f "$DEPLOY_PATH/.env" ]] || { echo "Missing protected $DEPLOY_PATH/.env" >&2; exit 2; }

cd "$DEPLOY_PATH"
chmod 700 scripts/deploy.sh scripts/backup-db.sh

if [[ "${GHCR_PULL_TOKEN_FROM_STDIN:-}" == "1" ]]; then
  docker login ghcr.io --username "$GHCR_USERNAME" --password-stdin
elif [[ -n "${GHCR_PULL_TOKEN:-}" ]]; then
  printf '%s' "$GHCR_PULL_TOKEN" | docker login ghcr.io --username "$GHCR_USERNAME" --password-stdin
fi

compose=(docker compose --env-file .env -f docker-compose.prod.yml)
export APP_IMAGE APP_ENV_FILE=.env
"${compose[@]}" config --quiet

# Fetch the artifact before stopping writers; wait for dependencies before backup.
"${compose[@]}" pull
# Convert a running RDB-only broker before restarting it with AOF enabled.
# Redis performs the rewrite from its loaded dataset, including concurrent writes.
if [[ -n "$("${compose[@]}" ps --status running --quiet redis)" ]]; then
  # Redis status and loop variables belong to the container shell.
  # shellcheck disable=SC2016
  "${compose[@]}" exec -T redis sh -ec '
    result="$(redis-cli CONFIG SET appendonly yes)"
    [ "$result" = OK ]
    for attempt in $(seq 1 60); do
      persistence="$(redis-cli INFO persistence | tr -d "\r")"
      if printf "%s\n" "$persistence" | grep -qx "aof_enabled:1" \
        && printf "%s\n" "$persistence" | grep -qx "aof_rewrite_in_progress:0" \
        && printf "%s\n" "$persistence" | grep -qx "aof_rewrite_scheduled:0" \
        && printf "%s\n" "$persistence" | grep -qx "aof_last_bgrewrite_status:ok"; then
        exit 0
      fi
      sleep 1
    done
    echo "Redis AOF rewrite did not complete successfully" >&2
    exit 1
  '
else
  # Inspect offline data before a new AOF-enabled container can open the volume.
  # shellcheck disable=SC2016
  redis_data_state="$("${compose[@]}" run --rm --no-deps --user redis redis sh -ec '
    if [ -f /data/dump.rdb ] && [ ! -f /data/appendonlydir/appendonly.aof.manifest ]; then
      printf legacy-rdb
    else
      printf ready
    fi
  ')"
  if [[ "$redis_data_state" == legacy-rdb ]]; then
    echo "Existing Redis has an offline RDB snapshot. Start it with its previous RDB-only configuration and rerun deploy to convert its loaded data to AOF." >&2
    exit 2
  fi
fi
"${compose[@]}" up -d --wait --wait-timeout 120 db redis
"${compose[@]}" stop --timeout 60 admin celery_worker_scraper celery_worker_bot celery_beat bot
./scripts/backup-db.sh
"${compose[@]}" run --rm --no-deps migrate
"${compose[@]}" run --rm --no-deps bootstrap
"${compose[@]}" up -d --no-deps --wait --wait-timeout 120 admin celery_worker_scraper celery_worker_bot celery_beat bot
# Recreate the proxy after the new admin is ready so its upstream DNS is refreshed.
"${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 120 nginx

"${compose[@]}" exec -T admin uv run --no-sync --no-dev python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()"

expected_services=(db redis admin nginx celery_worker_scraper celery_worker_bot celery_beat bot)
running_services="$("${compose[@]}" ps --status running --services)"
for service in "${expected_services[@]}"; do
  grep -Fxq "$service" <<< "$running_services" || { echo "Service is not running: $service" >&2; exit 1; }
done

echo "Deployment of $APP_IMAGE completed successfully."
