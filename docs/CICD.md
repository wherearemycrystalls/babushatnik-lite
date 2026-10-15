# CI/CD и эксплуатация

## Контур поставки

```text
PR / main ──> CI: lint + audit + PostgreSQL tests + Compose/build
                         │
tag vX.Y.Z ──────────────┴──> GHCR image (tag + SHA, SBOM, provenance)
                                      │
manual Deploy production ──> resolve OCI digest ─> backup ─> migrate ─> bootstrap ─> readiness
```

`main` и pull request проходят `.github/workflows/ci.yml`: Actionlint и
ShellCheck, Python через `uv python install`, lockfile через `uv sync --locked`,
Ruff, компиляция Python, `pip-audit`, тесты с реальным PostgreSQL,
проверка обеих Compose-конфигураций и сборка
контейнера.

Версию Python задаёт `.python-version`; локальные и CI-команды используют
`uv run --locked`. Образ устанавливает зависимости через uv при сборке,
а entrypoint запускает сервисы через `uv run --no-sync --no-dev`.

`security.yml` добавляет dependency review для PR и CodeQL по расписанию и при
изменениях main.

Тег `v*` запускает `release.yml`. Он повторно вызывает CI, публикует образ в
GHCR с тегом релиза и полным SHA коммита, формирует SBOM и provenance. Образ
`latest` не публикуется. Production получает не тег, а digest, разрешённый из
выбранного тега непосредственно перед deploy.

## Подготовка GitHub

Репозиторий должен иметь ветку `main` с защитой: обязательные review, запрет
force-push, обязательные статусы **Workflow and shell checks**, **Quality**,
**Tests (PostgreSQL)** и **Container and Compose**, а также включённые CodeQL
alerts. Для GitHub
Environment `production` добавьте required reviewers и следующие secrets:

| Secret | Назначение |
|---|---|
| `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_PATH` | Адрес, непривилегированный пользователь и абсолютный каталог релиза на сервере. |
| `DEPLOY_SSH_PRIVATE_KEY` | Отдельный ключ CI с минимальными правами. |
| `DEPLOY_KNOWN_HOSTS` | Точная строка host key production-сервера. |
| `GHCR_USERNAME`, `GHCR_PULL_TOKEN` | Read-only package token для pull на сервере. |

`GITHUB_TOKEN` достаточно для публикации пакета, если в настройках Actions
разрешено `Read and write permissions`. Он не передаётся на сервер.

## Первый деплой и обычный релиз

1. На сервере создайте каталог из `DEPLOY_PATH`, установите Docker Compose и
   скопируйте [.env.production.example](../.env.production.example) в
   `DEPLOY_PATH/.env`. Заполните секреты, установите права `600`.
   Для существующего развёртывания задайте в `.env` прежний
   `COMPOSE_PROJECT_NAME`, чтобы сохранить привязку к существующим volumes.
   Production по умолчанию использует `babushatnik-lite`; локальный стек —
   отдельный `babushatnik-lite-local`.
2. Создайте и отправьте подписанный тег `vX.Y.Z`. Дождитесь зелёного `Release
   image` и проверьте его GHCR attestation/SBOM.
3. Запустите `Deploy production`: сначала dry-run с тегом, затем новый запуск с
   `dry_run=false`. Environment approval является обязательной точкой контроля.

На сервер передаются только `docker-compose.prod.yml`, Nginx-конфиг и два
скрипта. `.env` остаётся на сервере. Скрипт останавливает writers, делает backup до миграции,
проверяет readiness `/healthz` с доступом к PostgreSQL и завершится с ошибкой,
если хотя бы один постоянный сервис не работает.

## Откат и восстановление

Откат приложения — это повторный manual deploy предыдущего существующего тега;
перед запуском он вновь фиксируется в конкретный digest.
Миграции не откатываются автоматически: сначала оцените совместимость кода со
схемой и восстановите дамп только по согласованной процедуре. Дампы создаются в
`$DEPLOY_PATH/backups` с правами владельца; срок хранения задаёт
`BACKUP_RETENTION_DAYS` (по умолчанию 14 дней). Проверьте дамп в изолированной
БД перед восстановлением и остановите writer-сервисы на время восстановления.

## Обновления и наблюдаемость

Dependabot еженедельно создаёт PR для Python, Docker и GitHub Actions.
Обновления pinned digest проверяются тем же CI. Health endpoint `/healthz` не
требует авторизации и отвечает `503`, если PostgreSQL недоступен; он предназначен
для внутренней Docker-проверки и не опубликован наружу отдельно от admin Nginx.
