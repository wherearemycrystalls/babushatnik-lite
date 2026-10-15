from __future__ import annotations

from celery import Celery
from kombu import Queue

import config

celery_app = Celery(
    "babushatnik-lite",
    broker=config.CELERY_BROKER_URL,
    backend=config.CELERY_RESULT_BACKEND,
    include=["background.tasks"],
)

celery_app.conf.update(
    accept_content=["json"],
    result_serializer="json",
    task_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_queues=(
        Queue("scraper"),
        Queue("bot"),
    ),
    task_routes={
        "background.tasks.scheduler_tick": {"queue": "scraper"},
        "background.tasks.load_new_listings": {"queue": "scraper"},
        "background.tasks.check_favourite_availability": {"queue": "scraper"},
        "background.tasks.full_scrape": {"queue": "scraper"},
        "background.tasks.process_listing_notifications": {"queue": "bot"},
    },
    beat_schedule={
        "scheduler-tick-every-minute": {
            "task": "background.tasks.scheduler_tick",
            "schedule": 60.0,
            "options": {"queue": "scraper"},
        },
        "check-favourite-availability": {
            "task": "background.tasks.check_favourite_availability",
            "schedule": float(config.AVAILABILITY_CHECK_INTERVAL_SECONDS),
            "options": {"queue": "scraper"},
        },
        "send-listing-notifications": {
            "task": "background.tasks.process_listing_notifications",
            "schedule": float(config.NOTIFICATION_SCAN_INTERVAL_SECONDS),
            "options": {"queue": "bot"},
        },
    },
)
