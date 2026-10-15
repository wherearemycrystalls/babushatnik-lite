.DEFAULT_GOAL := check
.PHONY: install format lint audit test check migrate bootstrap admin bot worker-scraper worker-bot beat compose-config build
.PHONY: local-config local-up local-infra local-down local-logs local-ps local-restart-workers prod-config prod-up prod-down prod-logs prod-ps

UV ?= uv
LOCAL_ENV ?= .env
PROD_ENV ?= .env.production
LOCAL_COMPOSE = APP_ENV_FILE=$(LOCAL_ENV) docker compose --env-file $(LOCAL_ENV) -f docker-compose.yml
PROD_COMPOSE = APP_ENV_FILE=$(PROD_ENV) docker compose --env-file $(PROD_ENV) -f docker-compose.prod.yml

install:
	$(UV) python install
	$(UV) sync --locked --group dev

format:
	$(UV) run --locked ruff format .

lint:
	$(UV) run --locked ruff format --check .
	$(UV) run --locked ruff check .
	$(UV) run --locked python -m compileall -q admin background bot kufar_scraper

audit:
	$(UV) run --locked pip-audit

test:
	$(UV) run --locked python -m unittest discover -s tests -v

check: lint audit test

migrate:
	$(UV) run --locked python -m background.migrate

bootstrap:
	$(UV) run --locked python -m background.bootstrap

admin:
	$(UV) run --locked python -m uvicorn admin.app:app --host 127.0.0.1 --port 8001 --reload

bot:
	$(UV) run --locked python -m bot.main

worker-scraper:
	$(UV) run --locked celery -A background.celery_app:celery_app worker -Q scraper -n scraper@%h --concurrency=2 --loglevel=INFO

worker-bot:
	$(UV) run --locked celery -A background.celery_app:celery_app worker -Q bot -n bot-worker@%h --concurrency=1 --loglevel=INFO

beat:
	$(UV) run --locked celery -A background.celery_app:celery_app beat --loglevel=INFO

compose-config:
	APP_ENV_FILE=.env.example docker compose --env-file .env.example -f docker-compose.yml config --quiet
	APP_ENV_FILE=.env.production.example APP_IMAGE=ghcr.io/example/babushatnik-lite@sha256:0000000000000000000000000000000000000000000000000000000000000000 docker compose --env-file .env.production.example -f docker-compose.prod.yml config --quiet

local-config:
	$(LOCAL_COMPOSE) config --quiet

local-up:
	$(LOCAL_COMPOSE) up --detach --build --wait --wait-timeout 120

local-infra:
	$(LOCAL_COMPOSE) up --detach --wait --wait-timeout 60 db redis

local-down:
	$(LOCAL_COMPOSE) down

local-logs:
	$(LOCAL_COMPOSE) logs --follow --tail 100

local-ps:
	$(LOCAL_COMPOSE) ps --all

local-restart-workers:
	$(LOCAL_COMPOSE) restart celery_worker_scraper celery_worker_bot celery_beat bot

prod-config:
	$(PROD_COMPOSE) config --quiet

# Initial startup only. Updates must use scripts/deploy.sh to stop writers and back up the DB.
prod-up:
	$(PROD_COMPOSE) up --detach --wait --wait-timeout 120

prod-down:
	$(PROD_COMPOSE) down

prod-logs:
	$(PROD_COMPOSE) logs --follow --tail 100

prod-ps:
	$(PROD_COMPOSE) ps --all

build:
	docker build --tag babushatnik-lite:local .
