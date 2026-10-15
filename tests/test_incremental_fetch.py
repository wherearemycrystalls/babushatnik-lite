from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from kufar_scraper.models.schemas import CityConfig
from kufar_scraper.scraper.client import KnownAdState, KufarClient
from kufar_scraper.scraper.parser import parse_source_time


def _ad(ad_id, timestamp="2026-10-15T10:00:00Z", price="30000"):
    return {"ad_id": ad_id, "list_time": timestamp, "price_usd": price}


def _state(ad):
    return KnownAdState(parse_source_time(ad.get("list_time")), copy.deepcopy(ad), True)


def _page(ads, next_token=None):
    return {"ads": ads, "pagination": {"pages": [{"label": "next", "token": next_token}] if next_token else []}}


class FakeKufarClient(KufarClient):
    def __init__(self, pages):
        super().__init__()
        self.pages = pages
        self.detail_calls = []

    async def _get(self, *args, **kwargs):
        result = self.pages.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def _fetch_detail(self, ad):
        self.detail_calls.append(ad["ad_id"])
        return {"result": {**ad, "body": f"full body {ad['ad_id']}"}}


class IncrementalFetchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.delay = patch("kufar_scraper.scraper.client.PAGE_DELAY", 0)
        self.delay.start()
        self.addCleanup(self.delay.stop)
        self.city = CityConfig(name="grodno", rgn="3", ar="9")

    async def test_unchanged_known_search_snapshots_are_saved_without_detail_requests(self):
        ads = [_ad(1), _ad(2)]
        client = FakeKufarClient([_page(ads)])
        result = await client.fetch_new_ads(self.city, {ad["ad_id"]: _state(ad) for ad in ads}, max_pages=5)
        self.assertTrue(result.completed)
        self.assertEqual(set(result.known_ids_seen), {1, 2})
        self.assertEqual(client.detail_calls, [])
        self.assertEqual(len(result.ads), 2)
        self.assertIn("_observed_at", result.ads[0])

    async def test_changed_list_time_does_not_stop_at_existing_id(self):
        old = _ad(1)
        ads = [_ad(1, "2026-10-15T11:00:00Z"), _ad(2), _ad(3)]
        client = FakeKufarClient([_page([ads[0]], "a"), _page([ads[1]], "b"), _page([ads[2]], "c")])
        states = {1: _state(old), 2: _state(ads[1]), 3: _state(ads[2])}
        result = await client.fetch_new_ads(self.city, states, max_pages=5)
        self.assertTrue(result.stopped_on_existing_page)
        self.assertEqual(result.pages, 3)
        self.assertEqual(client.detail_calls, [1])

    async def test_price_change_without_list_time_change_is_refreshed(self):
        old = _ad(1)
        client = FakeKufarClient([_page([_ad(1, price="35000")])])
        await client.fetch_new_ads(self.city, {1: _state(old)}, max_pages=5)
        self.assertEqual(client.detail_calls, [1])

    async def test_old_id_without_metadata_is_enriched(self):
        client = FakeKufarClient([_page([_ad(1)])])
        await client.fetch_new_ads(self.city, {1: KnownAdState(None, {}, False)}, max_pages=5)
        self.assertEqual(client.detail_calls, [1])

    async def test_page_cap_is_explicit_instead_of_a_successful_150_listing_catalog(self):
        pages = [_page([_ad(page * 30 + i) for i in range(30)], f"page{page + 1}") for page in range(5)]
        client = FakeKufarClient(pages)
        result = await client.fetch_new_ads(self.city, {}, max_pages=5)
        self.assertTrue(result.hit_page_limit)
        self.assertFalse(result.completed)
        self.assertEqual(result.fetched, 150)
        self.assertIn("max_pages=5", result.error)

    async def test_network_error_is_not_reported_as_success(self):
        result = await FakeKufarClient([RuntimeError("offline")]).fetch_new_ads(self.city, {}, max_pages=5)
        self.assertFalse(result.completed)
        self.assertEqual(result.error, "offline")

    async def test_quick_mode_reads_the_configured_pages_even_when_they_are_known(self):
        ads = [_ad(1), _ad(2), _ad(3), _ad(4)]
        client = FakeKufarClient([_page([ad], f"next-{ad['ad_id']}") for ad in ads])
        result = await client.fetch_new_ads(
            self.city, {ad["ad_id"]: _state(ad) for ad in ads}, max_pages=3, stop_on_known=False
        )
        self.assertEqual(result.pages, 3)
        self.assertTrue(result.hit_page_limit)
        self.assertFalse(result.stopped_on_existing_page)
        self.assertEqual(client.detail_calls, [])
