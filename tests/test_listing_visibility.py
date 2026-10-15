from __future__ import annotations

import unittest

from sqlalchemy import select

from bot.services.listing_service import visible_listing_clause
from kufar_scraper.db.models import Listing


class ListingVisibilityTests(unittest.TestCase):
    def test_visible_predicate_requires_source_and_admin_visibility(self) -> None:
        query = str(select(Listing.ad_id).where(visible_listing_clause()))

        self.assertIn("listings.is_source_active IS true", query)
        self.assertIn("listings.is_hidden_by_admin IS false", query)

    def test_listing_model_has_no_ml_columns(self) -> None:
        columns = Listing.__table__.c.keys()
        self.assertNotIn("renovation_type", columns)
        self.assertNotIn("distance_to_center", columns)
        self.assertNotIn("price_delta", columns)
