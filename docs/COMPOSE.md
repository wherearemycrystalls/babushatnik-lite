# Локальный и production Compose

Оба файла самостоятельные: production не накладывается поверх локального.

| | Local: `docker-compose.yml` | Production: `docker-compose.prod.yml` |
|---|---|---|
| Compose project | `babushatnik-lite-local` | `babushatnik-lite` |
| Env по умолчанию | `.env` | `.env.production` |
| Приложение | Сборка `babushatnik-lite:local` | Готовый `APP_IMAGE` с digest |
| Исходники | Bind mounts только кода, read-only | Код из образа |
| Админка | `http://127.0.0.1:8001`, Uvicorn reload | Nginx на `127.0.0.1:8081`, без reload |
| PostgreSQL / Redis | `127.0.0.1:5432` / `127.0.0.1:6379` | Только внутренняя сеть Compose |
| Telegram auth админки | По умолчанию выключена | Включена; токен и список ID обязательны |

`-p` или `COMPOSE_PROJECT_NAME` переопределяют имя проекта. Для **существующего
развёртывания** сохранить прежнее имя: иначе Docker создаст новые пустые volumes.
Старые volumes Finder не переносятся автоматически. Перед переключением
существующей БД выполнить процедуру из [LISTINGS_PIPELINE.md](LISTINGS_PIPELINE.md).

## Сервисы и порядок запуска

```text
PostgreSQL healthy -> migrate (exit 0) -> bootstrap (exit 0) -> приложения
Redis healthy -------------------------------------------> приложения
Admin healthy -------------------------------------------> Nginx (prod)
```

`migrate` и `bootstrap` — одноразовые процессы; их успешный статус `Exited (0)`.
Порядок задан через [условия depends_on](https://docs.docker.com/compose/how-tos/startup-order/).

| Сервис | Назначение / проверка |
|---|---|
| `db` | PostgreSQL; `pg_isready` с настроенными пользователем и БД |
| `redis` | Брокер Celery и FSM бота; `redis-cli ping` |
| `admin` | FastAPI; `/healthz` проверяет PostgreSQL |
| `celery_worker_scraper` | Очередь `scraper`; адресный Celery ping |
| `celery_worker_bot` | Очередь `bot`; адресный Celery ping |
| `celery_beat` | Периодические задачи; проверка PID процесса |
| `bot` | Telegram polling; процесс контролируется restart policy |
| `nginx` | Production reverse proxy; `/healthz` через upstream |

Скрейпер использует два процесса (`--concurrency=2`): долгий полный рефреш
не занимает единственный слот быстрого поиска. Планировщик не дублирует
ожидающие и выполняющиеся задания одного режима.

`--wait` проверяет healthchecks, а для polling-бота — запуск процесса.
Действительность Telegram-токена проверяется самим ботом; ошибки видны в логах.

## Local

На новой машине:

```bash
cp .env.example .env
# Заполнить токены отдельного локального Telegram-бота.
make local-config
make local-up
make local-ps
```

Если `.env` уже есть, обновить его по примеру, сохранив нужные значения.
Использовать отдельный токен для local: одновременный polling одного бота
локально и в production конфликтует.

`LOCAL_POSTGRES_PORT`, `LOCAL_REDIS_PORT`, `LOCAL_ADMIN_PORT` меняют порты хоста.
В контейнерах используются `db:5432`, `redis:6379`, `admin:8000`.
`LOCAL_ENV=/path/to/local.env` выбирает другой env-файл через Make.

Админка перезагружается при изменении кода. Для Celery и бота выполнить
`make local-restart-workers`; пересборка для изменений Python-кода не нужна.
После `uv add` выполнить `make local-up` для пересборки зависимостей.
При новых миграциях сначала `make local-down`, затем `make local-up`,
чтобы остановить writers перед обновлением схемы.

`make local-logs` показывает логи; `make local-down` останавливает стек,
сохраняя данные. Для запуска приложения через uv с PostgreSQL/Redis в Docker
использовать `make local-infra`. Локальным процессам задать
`POSTGRES_HOST=127.0.0.1`, нужный `POSTGRES_PORT` и Redis URL хоста;
для Celery также `CELERY_BROKER_URL` и `CELERY_RESULT_BACKEND`.

## Production

Для первоначального запуска из рабочей папки:

```bash
cp .env.production.example .env.production
# Заполнить секреты и APP_IMAGE=ghcr.io/<owner>/<repo>@sha256:<digest>.
chmod 600 .env.production
make prod-config
make prod-up
make prod-ps
```

`PROD_ENV=/path/to/production.env` выбирает другой env-файл. Make передаёт его
и для Compose-интерполяции, и для `env_file` контейнеров. Прямой вызов Docker:

```bash
APP_ENV_FILE=.env.production \
  docker compose --env-file .env.production -f docker-compose.prod.yml ps
```

На сервере CI/CD сохраняется protected `.env`: deploy/backup-скрипты сами
задают `APP_ENV_FILE=.env`. Секреты PostgreSQL, Telegram и сессии обязательны.
`PROD_ADMIN_PORT` меняет loopback-порт Nginx; TLS и внешний доступ обеспечивает
reverse proxy или tunnel на хосте.

Обновления выполнять через `scripts/deploy.sh`, а не через `prod-up`:
проверка конфига и pull -> готовые DB/Redis -> остановка writers -> backup ->
migrate -> bootstrap -> приложения healthy -> пересоздание Nginx.
Пересоздание прокси обновляет адрес upstream после замены admin.
При ошибке миграции writers остаются остановленными; перед перезапуском проверить
логи и совместимость схемы. Релизы и восстановление описаны в [CICD.md](CICD.md).

## Данные и uv

Каждый проект имеет собственные `postgres_data`, `redis_data`, `celerybeat_data`.
Redis использует AOF с `appendfsync everysec`: при аварии хоста возможна потеря
последней секунды записей. При переводе существующего Redis с RDB на AOF
deploy-скрипт включает AOF в работающем Redis и ждёт успешного rewrite перед
пересозданием контейнера, чтобы сохранить загруженный snapshot.
Если старый Redis остановлен и остался только RDB snapshot, deploy завершится
до изменения данных: сначала запустить Redis с его прежней конфигурацией,
затем повторить deploy. См. [процедуру Redis](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/#how-i-can-switch-to-aof-if-im-currently-using-dumprdb-snapshots).

Состояние beat хранится в volume; PID и uv-cache — в `/tmp`. Приложение работает
от `app` (UID 10001), с read-only корнем и `no-new-privileges`.
Логи ограничены тремя файлами по 10 MB на сервис.

Зависимости устанавливаются при сборке; uv-entrypoint запускает команды без
синхронизации и скачиваний. `/opt/venv` содержит Linux-зависимости образа;
локальные `.venv` и `.env` не монтируются. См. [uv Docker guide](https://docs.astral.sh/uv/guides/integration/docker/).
