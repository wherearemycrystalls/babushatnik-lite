import asyncio
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiohttp

from kufar_scraper.models.schemas import CityConfig
from kufar_scraper.scraper.parser import parse_source_time, search_content

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.kufar.by/search-api/v1/search/rendered-paginated"
DETAILS_URL = "https://api.kufar.by/search-api/v1/ads/{}/rendered?lang=ru"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Origin": "https://re.kufar.by",
    "Referer": "https://re.kufar.by/",
}

PAGE_DELAY = 2.0  # delay between pages
DETAIL_DELAY = 0.5  # delay between detail requests
MAX_RETRIES = 5  # max retry attempts on 429
RETRY_BASE = 5.0  # base pause on 429 (seconds, doubles each attempt)


@dataclass(slots=True)
class KnownAdState:
    list_time: datetime | None
    raw_search: dict
    has_details: bool


@dataclass(slots=True)
class FetchResult:
    ads: list[dict] = field(default_factory=list)
    pages: int = 0
    fetched: int = 0
    stopped_on_existing_page: bool = False
    known_ids_seen: list[int] = field(default_factory=list)
    source_total: int | None = None
    completed: bool = False
    hit_page_limit: bool = False
    error: str | None = None


@dataclass(slots=True)
class FullFetchResult(FetchResult):
    pass


class KufarClient:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "KufarClient":
        self._session = aiohttp.ClientSession(headers=HEADERS)
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._session:
            await self._session.close()

    async def _get(self, url: str, *, params: dict | None = None, timeout: int = 15) -> dict:
        """GET with automatic retry on 429 (exponential backoff)."""
        delay = RETRY_BASE
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with self._session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status == 429:
                        retry_after = float(resp.headers.get("Retry-After", delay))
                        wait = max(retry_after, delay)
                        logger.warning(
                            "429 Too Many Requests — waiting %.0fs (attempt %d/%d)",
                            wait,
                            attempt,
                            MAX_RETRIES,
                        )
                        await asyncio.sleep(wait)
                        delay *= 2
                        continue
                    resp.raise_for_status()
                    return await resp.json()
            except aiohttp.ClientResponseError:
                raise
            except Exception as exc:
                if attempt == MAX_RETRIES:
                    raise
                logger.warning("Network error (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
                await asyncio.sleep(delay)
                delay *= 2
        raise RuntimeError(f"Failed to get response after {MAX_RETRIES} attempts: {url}")

    async def fetch_all_ads(
        self, city: CityConfig, max_pages: int = 0, log_callback=None, *, fetch_details: bool = True, ads_callback=None
    ) -> FullFetchResult:
        """Walk all source cursors; an optional positive page cap fails closed."""
        result = await self._fetch_pages(
            city, max_pages=max_pages, log_callback=log_callback, fetch_details=fetch_details, ads_callback=ads_callback
        )
        return FullFetchResult(**{name: getattr(result, name) for name in FetchResult.__dataclass_fields__})

    async def fetch_new_ads(
        self,
        city: CityConfig,
        known_ad_states: dict[int, KnownAdState],
        max_pages: int,
        log_callback=None,
        known_page_overlap: int = 2,
        stop_on_known: bool = True,
        ads_callback=None,
    ) -> FetchResult:
        """Refresh new/changed IDs; cross consecutive unchanged pages before stopping."""
        if max_pages < 1:
            raise ValueError("Incremental max_pages must be positive")
        return await self._fetch_pages(
            city,
            max_pages=max_pages,
            known=known_ad_states,
            known_page_overlap=max(1, known_page_overlap),
            stop_on_known=stop_on_known,
            log_callback=log_callback,
            ads_callback=ads_callback,
        )

    async def _fetch_pages(
        self,
        city,
        *,
        max_pages,
        known=None,
        known_page_overlap=2,
        stop_on_known=True,
        log_callback=None,
        fetch_details=True,
        ads_callback=None,
    ):
        if max_pages < 0:
            raise ValueError("max_pages cannot be negative")
        result = FetchResult()
        cursor = None
        used_cursors = set()
        unchanged_pages = 0
        seen_ids = set()

        def log(message):
            logger.info(message)
            if log_callback:
                log_callback(message)

        while max_pages == 0 or result.pages < max_pages:
            page = result.pages + 1
            params = {"cat": "1010", "typ": "let", "rgn": city.rgn, "size": "30", "lang": "ru"}
            if city.ar and city.ar.strip().lower() not in {"", "0", "none"}:
                params["ar"] = city.ar
            if cursor:
                params["cursor"] = cursor
            log(f"[{city.name}] Fetching {'fresh ' if known is not None else ''}page {page}...")
            try:
                data = await self._get(SEARCH_URL, params=params)
                if not isinstance(data, dict) or not isinstance(data.get("ads"), list):
                    raise ValueError("Malformed search response: ads array is missing")
                pagination = data.get("pagination")
                if not isinstance(pagination, dict) or not isinstance(pagination.get("pages"), list):
                    raise ValueError("Malformed pagination: pages array is missing")
                if any(not isinstance(item, dict) for item in pagination["pages"]):
                    raise ValueError("Malformed pagination entry")
            except Exception as exc:
                result.error = str(exc)
                log(f"[{city.name}] Error on page {page}: {exc}")
                return result
            observed_at = datetime.now(timezone.utc).isoformat()
            result.pages += 1
            page_ads = data["ads"]
            result.fetched += len(page_ads)
            total = data.get("total")
            if isinstance(total, int) and not isinstance(total, bool):
                result.source_total = total
            if not page_ads:
                if result.source_total and len(seen_ids) < result.source_total:
                    result.error = "Source returned an empty page before the advertised catalog was collected"
                else:
                    result.completed = True
                return result

            page_items = []
            unchanged_page = known is not None
            new_ids_on_page = set()
            for ad in page_ads:
                if not isinstance(ad, dict) or not isinstance(ad.get("ad_id"), int):
                    result.error = "Malformed listing: integer ad_id is missing"
                    return result
                ad_id = ad["ad_id"]
                new_ids_on_page.add(ad_id)
                state = known.get(ad_id) if known is not None else None
                changed = (
                    state is None
                    or not state.has_details
                    or state.list_time is None
                    or (parse_source_time(ad.get("list_time")) != state.list_time)
                    or search_content(ad) != search_content(state.raw_search)
                )
                if changed:
                    unchanged_page = False
                item = deepcopy(ad)
                item.update(_raw_search=deepcopy(ad), _city=city.name, _observed_at=observed_at)
                if changed and fetch_details:
                    response = await self._fetch_detail(ad)
                    if response is not None:
                        item["_detail_response"] = response
                        item["_detail_fetched_at"] = datetime.now(timezone.utc).isoformat()
                    else:
                        item["_detail_failed"] = True
                else:
                    result.known_ids_seen.append(ad_id)
                result.ads.append(item)
                page_items.append(item)
            if ads_callback is not None:
                await ads_callback(page_items)
            repeated_page = new_ids_on_page.issubset(seen_ids)
            seen_ids.update(new_ids_on_page)
            unchanged_pages = unchanged_pages + 1 if unchanged_page else 0
            if stop_on_known and known is not None and unchanged_pages >= known_page_overlap:
                result.stopped_on_existing_page = True
                result.completed = True
                log(f"[{city.name}] Reached {unchanged_pages} unchanged known pages; incremental boundary reached.")
                return result
            next_page = next((p for p in pagination["pages"] if p.get("label") == "next"), None)
            if not next_page:
                if known is None and result.source_total is not None and len(seen_ids) < result.source_total:
                    result.error = "Pagination ended before the advertised catalog was collected"
                    return result
                result.completed = True
                return result
            next_cursor = next_page.get("token")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in used_cursors or repeated_page:
                result.error = "Source pagination repeated or returned an invalid next cursor"
                return result
            used_cursors.add(next_cursor)
            cursor = next_cursor
            await asyncio.sleep(PAGE_DELAY)

        result.hit_page_limit = True
        result.error = f"reached max_pages={max_pages} before source pagination ended"
        log(f"[{city.name}] Incomplete: {result.error}; source total={result.source_total}")
        return result

    async def _fetch_detail(self, ad: dict) -> dict | None:
        await asyncio.sleep(DETAIL_DELAY)
        try:
            response = await self._get(DETAILS_URL.format(ad["ad_id"]), timeout=10)
            detail = response.get("result", response) if isinstance(response, dict) else None
            if not isinstance(detail, dict) or detail.get("ad_id") != ad["ad_id"]:
                raise ValueError("Detail response does not match requested ad_id")
            return response
        except Exception as exc:
            logger.warning("Detail fetch failed for %s: %s", ad.get("ad_id"), exc)
            return None
