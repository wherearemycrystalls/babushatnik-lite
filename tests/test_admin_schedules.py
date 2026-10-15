import asyncio
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

from sqlalchemy import delete, select

from kufar_scraper.db.client import dispose_engine, get_session, init_engine, seed_default_task_schedules
from kufar_scraper.db.models import TaskRun, TaskSchedule

URL = os.getenv("TEST_DATABASE_URL")


async def request(path, method="GET", form=None):
    from admin.app import app

    messages = []
    body = urlencode(form or {}).encode()
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Future()

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/x-www-form-urlencoded"), (b"host", b"test")],
        "server": ("test", 80),
        "client": ("127.0.0.1", 1000),
    }
    with (
        patch("config.ADMIN_TELEGRAM_AUTH_ENABLED", False),
        patch("admin.app._inspect_workers", return_value={"online": 0, "workers": [], "error": None}),
    ):
        await app(scope, receive, send)
    return next(m["status"] for m in messages if m["type"] == "http.response.start"), b"".join(
        m.get("body", b"") for m in messages if m["type"] == "http.response.body"
    )


@unittest.skipUnless(URL, "set TEST_DATABASE_URL for PostgreSQL tests")
class AdminScheduleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        init_engine(URL)
        async with get_session() as session:
            await session.execute(delete(TaskRun))
            for key, interval in [("new_listings", 300), ("full_reconciliation", 86400)]:
                schedule = await session.get(TaskSchedule, key)
                schedule.enabled = True
                schedule.interval_seconds = interval
                schedule.max_pages = 5 if key == "new_listings" else None
                schedule.next_run_at = datetime.now(timezone.utc) + timedelta(hours=1)
            await session.commit()

    async def asyncTearDown(self):
        await dispose_engine()

    async def test_both_schedule_forms_and_status_expose_independent_settings(self):
        status, body = await request("/admin/tasks")
        self.assertEqual(status, 200)
        html = body.decode()
        self.assertIn('name="max_pages"', html)
        self.assertIn("/tasks/schedules/full-reconciliation", html)
        self.assertIn("Full refresh · all pages", html)
        self.assertEqual(
            (
                await request(
                    "/admin/tasks/schedules/new-listings",
                    "POST",
                    {"interval_minutes": 3, "max_pages": 7, "enabled": "on"},
                )
            )[0],
            303,
        )
        self.assertEqual(
            (
                await request(
                    "/admin/tasks/schedules/full-reconciliation", "POST", {"interval_minutes": 20, "enabled": "on"}
                )
            )[0],
            303,
        )
        status, body = await request("/admin/tasks/status")
        payload = json.loads(body)
        self.assertEqual((payload["schedule"]["interval_minutes"], payload["schedule"]["max_pages"]), (3, 7))
        self.assertEqual(payload["full_schedule"]["interval_minutes"], 20)
        async with get_session() as session:
            await seed_default_task_schedules(
                session,
                new_listings_interval_seconds=60,
                full_reconciliation_interval_seconds=86400,
                new_listings_max_pages=5,
            )
            self.assertEqual((await session.get(TaskSchedule, "new_listings")).max_pages, 7)
            self.assertEqual((await session.get(TaskSchedule, "full_reconciliation")).interval_seconds, 1200)

    async def test_invalid_values_do_not_silently_change_schedules(self):
        for form in [
            {"interval_minutes": 0, "max_pages": 2},
            {"interval_minutes": 2, "max_pages": 0},
            {"interval_minutes": -1, "max_pages": 2},
        ]:
            self.assertEqual((await request("/admin/tasks/schedules/new-listings", "POST", form))[0], 422)
        self.assertEqual(
            (await request("/admin/tasks/schedules/full-reconciliation", "POST", {"interval_minutes": 0}))[0], 422
        )
        async with get_session() as session:
            self.assertEqual((await session.get(TaskSchedule, "new_listings")).max_pages, 5)
            self.assertEqual((await session.get(TaskSchedule, "full_reconciliation")).interval_seconds, 86400)

    async def test_full_schedule_can_be_disabled_without_disabling_quick_search(self):
        await request("/admin/tasks/schedules/full-reconciliation", "POST", {"interval_minutes": 30})
        async with get_session() as session:
            self.assertFalse((await session.get(TaskSchedule, "full_reconciliation")).enabled)
            self.assertTrue((await session.get(TaskSchedule, "new_listings")).enabled)

    async def test_running_full_refresh_is_not_enqueued_again_by_a_short_timer(self):
        from background.tasks import _scheduler_tick

        async with get_session() as session:
            for row in (await session.scalars(select(TaskSchedule))).all():
                row.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=2)
            session.add(TaskRun(task_name="full_scrape", status="running"))
            await session.commit()
        with (
            patch("background.tasks.full_scrape.apply_async", return_value=SimpleNamespace(id="test-full")) as full,
            patch(
                "background.tasks.load_new_listings.apply_async", return_value=SimpleNamespace(id="test-quick")
            ) as quick,
        ):
            result = await _scheduler_tick()
            self.assertEqual(result["enqueued"], 1)
            full.assert_not_called()
            quick.assert_called_once()
            self.assertEqual((await _scheduler_tick())["enqueued"], 0)
