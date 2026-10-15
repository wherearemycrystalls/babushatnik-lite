from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from background.task_db import as_utc


class TaskTimeTests(unittest.TestCase):
    def test_as_utc_marks_naive_values_as_utc(self) -> None:
        value = datetime(2026, 8, 10, 12, 30)

        self.assertEqual(as_utc(value), datetime(2026, 8, 10, 12, 30, tzinfo=timezone.utc))

    def test_as_utc_converts_aware_values(self) -> None:
        value = datetime(2026, 8, 10, 15, 30, tzinfo=timezone(timedelta(hours=3)))

        self.assertEqual(as_utc(value), datetime(2026, 8, 10, 12, 30, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
