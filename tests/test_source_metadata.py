from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bot.utils.formatters import format_card, format_full_detail
from kufar_scraper.db.models import Listing
from kufar_scraper.models.schemas import KufarAdRaw
from kufar_scraper.scraper.client import KufarClient
from kufar_scraper.scraper.parser import parse_ad

SAMPLE = Path(__file__).parent / "fixtures" / "kufar_listing.json"


def payload():
    data = json.loads(SAMPLE.read_text())
    return {
        **data["search"],
        "_city": "grodno",
        "_observed_at": "2026-10-15T12:00:00Z",
        "_detail_response": data["detail"],
        "_detail_fetched_at": "2026-10-15T12:00:01Z",
    }


class SourceMetadataTests(unittest.TestCase):
    def test_preserves_source_json_and_all_parameter_metadata(self):
        raw = payload()
        original = copy.deepcopy(raw)
        parsed = parse_ad(raw)
        self.assertEqual(raw, original)
        self.assertEqual(parsed.list_time, datetime(2026, 10, 15, 11, 56, 37, tzinfo=timezone.utc))
        self.assertEqual(parsed.title, "Пример квартиры")
        self.assertEqual(parsed.price_usd, Decimal("315.48"))
        self.assertEqual(parsed.price_byn, Decimal("950.00"))
        self.assertEqual(parsed.price_eur, Decimal("281.85"))
        self.assertEqual(parsed.area_total, Decimal("71"))
        self.assertEqual(parsed.total_floors, 10)
        self.assertEqual(parsed.year_built, 2014)
        self.assertEqual(parsed.repair, "Косметический")
        self.assertEqual(parsed.latitude, Decimal("53.6"))
        self.assertEqual(parsed.parameters["condition"], "1")
        self.assertEqual(parsed.parameter_details["ad"]["flat_repair"]["vl"], "Косметический")
        self.assertEqual(parsed.raw_search["vendor_future_field"], {"nested": [1, True, None]})
        self.assertEqual(parsed.raw_detail, original["_detail_response"])
        self.assertEqual(parsed.description, original["_detail_response"]["result"]["body"])
        self.assertEqual(parsed.parameters["address_tags_yandex"], ["test-address"])
        KufarAdRaw.model_validate(parsed.raw_search)

    def test_missing_invalid_and_naive_source_times_are_not_replaced_with_ingestion_time(self):
        for value in [None, "invalid", "2026-10-15T11:56:37", "2036-10-15T11:56:37Z"]:
            with self.subTest(value=value):
                raw = payload()
                raw["list_time"] = value
                raw["_detail_response"]["result"]["list_time"] = value
                parsed = parse_ad(raw)
                self.assertIsNone(parsed.list_time)
                self.assertTrue(parsed.data_warnings)
                self.assertEqual(parsed.raw_search["list_time"], value)

    def test_card_and_details_show_source_features_and_exact_price(self):
        parsed = parse_ad(payload())
        listing = Listing(**parsed.model_dump())
        card = format_card(listing)
        detail = format_full_detail(listing)
        self.assertIn("$315.48", card)
        self.assertIn("15.10.2026 14:56", card)
        self.assertIn("71 м²", card)
        self.assertIn("Косметический", card)
        self.assertIn("Вторая строка", detail)
        self.assertLessEqual(len(card), 1024)
        self.assertLessEqual(len(detail), 4096)

    def test_markdown_in_source_text_is_escaped(self):
        parsed = parse_ad(payload())
        parsed.title = "[title](evil)*_test_"
        listing = Listing(**parsed.model_dump())
        self.assertIn(r"\[title](evil)\*\_test\_", format_card(listing))

    def test_large_source_text_does_not_break_telegram_limits(self):
        parsed = parse_ad(payload())
        parsed.title = "*" * 1000
        parsed.address = "_" * 500
        parsed.repair = "*" * 1000
        parsed.description = "[" * 10000
        listing = Listing(**parsed.model_dump())
        self.assertLessEqual(len(format_card(listing)), 1024)
        self.assertLessEqual(len(format_full_detail(listing)), 4096)


class DetailEnvelopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_result_envelope_without_discarding_fields(self):
        client = KufarClient()
        expected = payload()["_detail_response"]
        client._get = AsyncMock(return_value=expected)
        with patch("kufar_scraper.scraper.client.DETAIL_DELAY", 0):
            result = await client._fetch_detail({"ad_id": 1001})
        self.assertEqual(result, expected)

    async def test_wrong_listing_id_is_rejected(self):
        client = KufarClient()
        client._get = AsyncMock(return_value={"result": {"ad_id": 999, "body": "wrong apartment"}})
        with patch("kufar_scraper.scraper.client.DETAIL_DELAY", 0):
            self.assertIsNone(await client._fetch_detail({"ad_id": 1001}))
