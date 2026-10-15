from __future__ import annotations

import unittest
from unittest.mock import patch

from kufar_scraper.models.schemas import CityConfig
from kufar_scraper.scraper.client import KufarClient


def _page(ads: list[dict], next_token: str | None = None) -> dict:
    pages = [{"label": "next", "token": next_token}] if next_token else []
    return {"ads": ads, "pagination": {"pages": pages}}


class FakeKufarClient(KufarClient):
    def __init__(self, pages: list[dict | Exception]) -> None:
        self.pages = pages

    async def _get(self, url: str, *, params: dict | None = None, timeout: int = 15) -> dict:
        result = self.pages.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def _fetch_detail(self, ad: dict) -> dict:
        return {"result": {"ad_id": ad["ad_id"], "body": f"detail {ad['ad_id']}"}}


class FullReconciliationFetchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        delay = patch("kufar_scraper.scraper.client.PAGE_DELAY", 0)
        delay.start()
        self.addCleanup(delay.stop)

    async def test_marks_complete_only_after_source_pagination_ends(self) -> None:
        client = FakeKufarClient([_page([{"ad_id": 1}], next_token="n"), _page([{"ad_id": 2}])])
        result = await client.fetch_all_ads(CityConfig(name="grodno", rgn="3", ar="9"), max_pages=5)

        self.assertTrue(result.completed)
        self.assertIsNone(result.error)
        self.assertEqual(result.pages, 2)
        self.assertEqual([ad["ad_id"] for ad in result.ads], [1, 2])

    async def test_refuses_reconciliation_when_page_limit_is_reached(self) -> None:
        client = FakeKufarClient([_page([{"ad_id": 1}], next_token="n")])
        result = await client.fetch_all_ads(CityConfig(name="grodno", rgn="3", ar="9"), max_pages=1)

        self.assertFalse(result.completed)
        self.assertIn("max_pages", result.error or "")

    async def test_refuses_reconciliation_after_source_error(self) -> None:
        client = FakeKufarClient([RuntimeError("Kufar unavailable")])
        result = await client.fetch_all_ads(CityConfig(name="grodno", rgn="3", ar="9"), max_pages=5)

        self.assertFalse(result.completed)
        self.assertIn("Kufar unavailable", result.error or "")

    async def test_repeated_cursor_fails_closed(self):
        client = FakeKufarClient([_page([{"ad_id": 1}], "same"), _page([{"ad_id": 2}], "same")])
        result = await client.fetch_all_ads(CityConfig(name="grodno", rgn="3", ar="9"))
        self.assertFalse(result.completed)
        self.assertIn("pagination", result.error)

    async def test_default_full_fetch_has_no_100_page_cutoff(self):
        pages = [_page([{"ad_id": page}], f"next-{page}") for page in range(100)] + [_page([{"ad_id": 100}])]
        result = await FakeKufarClient(pages).fetch_all_ads(CityConfig(name="grodno", rgn="3", ar="9"))
        self.assertTrue(result.completed)
        self.assertEqual(result.pages, 101)

    async def test_malformed_search_cannot_archive_a_city(self):
        result = await FakeKufarClient([{"error": "bad upstream"}]).fetch_all_ads(
            CityConfig(name="grodno", rgn="3", ar="9")
        )
        self.assertFalse(result.completed)
        self.assertIn("Malformed", result.error)

    async def test_malformed_pagination_retains_received_pages_without_success(self):
        for broken in [{"ads": [{"ad_id": 2}]}, {"ads": [{"ad_id": 2}], "pagination": {"pages": [None]}}]:
            with self.subTest(broken=broken):
                result = await FakeKufarClient([_page([{"ad_id": 1}], "a"), broken]).fetch_all_ads(
                    CityConfig(name="grodno", rgn="3", ar="9")
                )
                self.assertFalse(result.completed)
                self.assertIn("pagination", result.error)
                self.assertEqual([ad["ad_id"] for ad in result.ads], [1])

    async def test_empty_page_with_uncollected_source_total_is_incomplete(self):
        result = await FakeKufarClient([_page([{"ad_id": 1}], "a"), {**_page([]), "total": 429}]).fetch_all_ads(
            CityConfig(name="grodno", rgn="3", ar="9")
        )
        self.assertFalse(result.completed)
        self.assertIn("advertised catalog", result.error)

    async def test_availability_scan_reads_all_pages_without_detail_requests(self):
        client = FakeKufarClient([_page([{"ad_id": 1}], "a"), _page([{"ad_id": 2}])])
        with patch.object(client, "_fetch_detail") as detail:
            result = await client.fetch_all_ads(CityConfig(name="grodno", rgn="3", ar="9"), fetch_details=False)
        self.assertTrue(result.completed)
        self.assertEqual(result.fetched, 2)
        detail.assert_not_called()

    async def test_pages_are_streamed_before_a_later_page_fails(self):
        from unittest.mock import AsyncMock

        callback = AsyncMock()
        client = FakeKufarClient([_page([{"ad_id": 1}], "a"), RuntimeError("offline")])
        result = await client.fetch_all_ads(CityConfig(name="grodno", rgn="3", ar="9"), ads_callback=callback)
        self.assertFalse(result.completed)
        callback.assert_awaited_once()
        self.assertEqual(callback.call_args.args[0][0]["ad_id"], 1)

    async def test_missing_next_page_cannot_archive_an_advertised_larger_catalog(self):
        result = await FakeKufarClient([{**_page([{"ad_id": 1}]), "total": 429}]).fetch_all_ads(
            CityConfig(name="grodno", rgn="3", ar="9"), fetch_details=False
        )
        self.assertFalse(result.completed)
        self.assertIn("advertised catalog", result.error)
