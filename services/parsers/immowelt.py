"""Провайдер Immowelt.de: JSON (__UFRN_* / __NEXT_DATA__) и HTML-карточки."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup, Tag

from services.parsers.base import BaseProvider, ListingData
from validators import parse_amount, parse_number, parse_sqm

logger = logging.getLogger(__name__)

BASE_URL = "https://www.immowelt.de"
REQUEST_TIMEOUT = 20.0
MAX_CONCURRENCY = 2

_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Referer": f"{BASE_URL}/",
}

_UMLAUTS: dict[str, str] = {
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "Ä": "ae", "Ö": "oe", "Ü": "ue",
}
_ROOMS = re.compile(r"(\d+(?:[.,]\d+)?)\s*Zimmer", re.IGNORECASE)
_SQM = re.compile(r"(\d+(?:[.,]\d+)?)\s*m²", re.IGNORECASE)
_WARM = re.compile(
    r"(?:warmmiete|warm[\s\-]?miete)\s*[:.]?\s*(\d[\d.,]*)",
    re.IGNORECASE,
)
_KALT = re.compile(
    r"(?:kaltmiete|kalt[\s\-]?miete)\s*[:.]?\s*(\d[\d.,]*)",
    re.IGNORECASE,
)
_EXPOSE_ID = re.compile(
    r"/expose/([0-9a-f-]{8,}|[0-9A-Z]{12,})",
    re.IGNORECASE,
)


class ImmoweltProvider(BaseProvider):
    name = "immowelt"

    async def fetch_listings(self, search_criteria: dict[str, Any]) -> list[ListingData]:
        city = str(search_criteria.get("city_de") or search_criteria.get("city") or "")
        max_pages = max(1, int(search_criteria.get("max_pages") or 1))
        url = build_search_url(
            city,
            budget_max=search_criteria.get("budget_max"),
            rooms_min=search_criteria.get("rooms_min"),
            sqm_min=search_criteria.get("sqm_min"),
            page=1,
        )
        listings: list[ListingData] = []
        seen_ids: set[str] = set()

        async with httpx.AsyncClient(
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        ) as client:
            for page in range(1, max_pages + 1):
                page_url = url if page == 1 else f"{url}&page={page}"
                try:
                    response = await client.get(page_url)
                    response.raise_for_status()
                except httpx.HTTPError as error:
                    if page == 1:
                        raise RuntimeError(f"Immowelt недоступен: {error}") from error
                    logger.warning("Immowelt: страница %d недоступна: %s", page, error)
                    break

                batch = _parse_search_html(response.text)
                if not batch and page == 1:
                    batch = _parse_next_data(response.text)
                added = 0
                for item in batch:
                    if item.id in seen_ids:
                        continue
                    seen_ids.add(item.id)
                    listings.append(item)
                    added += 1
                logger.info(
                    "Immowelt: страница %d, +%d (всего %d)", page, added, len(listings)
                )
                if added == 0:
                    break
                if page < max_pages:
                    await asyncio.sleep(1.0)

        logger.info("Immowelt: получено %d объявлений для %s", len(listings), city)
        return listings

    async def load_details(self, listings: list[ListingData]) -> None:
        if not listings:
            return
        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
        async with httpx.AsyncClient(
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        ) as client:
            await asyncio.gather(
                *(_load_one(client, listing, semaphore) for listing in listings)
            )


def city_to_slug(city: str) -> str:
    slug = city.strip().lower()
    for source, replacement in _UMLAUTS.items():
        slug = slug.replace(source.lower(), replacement)
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def build_search_url(
    city: str,
    *,
    budget_max: int | float | None = None,
    rooms_min: float | None = None,
    sqm_min: float | None = None,
    page: int = 1,
) -> str:
    """URL выдачи Immowelt: /liste/{stadt}/wohnungen/mieten?pr=&r=&sf=."""
    slug = city_to_slug(city)
    if not slug:
        raise ValueError(f"Не удалось построить slug для города {city!r}")
    params: dict[str, str | int | float] = {}
    if budget_max is not None and int(budget_max) > 0:
        params["pr"] = int(budget_max)
    if rooms_min is not None and float(rooms_min) > 0:
        rooms = float(rooms_min)
        params["r"] = int(rooms) if rooms.is_integer() else rooms
    if sqm_min is not None and float(sqm_min) > 0:
        params["sf"] = int(float(sqm_min))
    if page > 1:
        params["page"] = page
    query = f"?{urlencode(params)}" if params else ""
    return f"{BASE_URL}/liste/{slug}/wohnungen/mieten{query}"


def _parse_next_data(html: str) -> list[ListingData]:
    """Fallback: классический __NEXT_DATA__ (если Immowelt вернёт старую вёрстку)."""
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one("script#__NEXT_DATA__")
    if not node or not node.string:
        return []
    try:
        payload = json.loads(node.string)
    except json.JSONDecodeError:
        return []

    items: list[Any] = []
    page_props = payload.get("props", {}).get("pageProps", {})
    for key in ("classifieds", "searchResults", "results"):
        block = page_props.get(key)
        if isinstance(block, dict):
            nested = block.get("classifieds") or block.get("items") or block.get("results")
            if isinstance(nested, list):
                items.extend(nested)
        elif isinstance(block, list):
            items.extend(block)

    listings: list[ListingData] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        parsed = _listing_from_json_blob(raw)
        if parsed is not None:
            listings.append(parsed)
    return listings


def _listing_from_json_blob(raw: dict[str, Any]) -> ListingData | None:
    listing_id = str(
        raw.get("id")
        or raw.get("classifiedId")
        or raw.get("exposeId")
        or raw.get("onlineId")
        or ""
    ).strip()
    url = str(raw.get("url") or raw.get("link") or "").strip()
    if url and not url.startswith("http"):
        url = f"{BASE_URL}{url}"
    if not listing_id and url:
        match = _EXPOSE_ID.search(url)
        if match:
            listing_id = match.group(1)
    if not listing_id or not url:
        return None

    title = str(raw.get("title") or raw.get("headline") or "").strip()
    location = _extract_location(raw)
    price = _extract_price(raw)
    rooms = _extract_rooms(raw)
    sqm = _extract_sqm(raw)
    image = _extract_image(raw)
    if not title:
        title = f"Wohnung {location or listing_id}"

    return ListingData(
        id=listing_id,
        title=title,
        price=price,
        size_sqm=sqm,
        rooms=rooms,
        location=location,
        url=url,
        image_url=image,
        source_platform="immowelt",
        raw_data={"json": raw},
    )


def _extract_location(raw: dict[str, Any]) -> str | None:
    for key in ("address", "location", "city", "district"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            parts = [
                str(value.get(part) or "").strip()
                for part in ("district", "city", "zipCode", "street")
            ]
            joined = ", ".join(part for part in parts if part)
            if joined:
                return joined
    return None


def _extract_price(raw: dict[str, Any]) -> int | None:
    for key in ("price", "rent", "warmRent", "coldRent", "primaryPrice"):
        value = raw.get(key)
        if isinstance(value, dict):
            for sub in ("value", "amount", "primary", "warm", "cold"):
                parsed = parse_amount(str(value.get(sub) or ""))
                if parsed is not None:
                    return parsed
        parsed = parse_amount(str(value or ""))
        if parsed is not None:
            return parsed
    return None


def _extract_rooms(raw: dict[str, Any]) -> float | None:
    for key in ("rooms", "numberOfRooms", "roomCount"):
        parsed = parse_number(str(raw.get(key) or ""))
        if parsed is not None:
            return parsed
    return None


def _extract_sqm(raw: dict[str, Any]) -> float | None:
    for key in ("area", "livingSpace", "size", "squareMeters"):
        parsed = parse_sqm(str(raw.get(key) or ""))
        if parsed is not None:
            return parsed
    return None


def _extract_image(raw: dict[str, Any]) -> str | None:
    for key in ("image", "imageUrl", "picture", "thumbnail"):
        value = raw.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
        if isinstance(value, dict):
            url = value.get("url") or value.get("src")
            if isinstance(url, str) and url.startswith("http"):
                return url
    gallery = raw.get("gallery") or raw.get("pictures")
    if isinstance(gallery, list) and gallery:
        first = gallery[0]
        if isinstance(first, str) and first.startswith("http"):
            return first
        if isinstance(first, dict):
            url = first.get("url") or first.get("src")
            if isinstance(url, str):
                return url
    return None


def _parse_search_html(html: str) -> list[ListingData]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select('[data-testid^="classified-card-mfe-"]')
    if not cards:
        cards = soup.select('[data-testid="serp-core-classified-card-testid"]')

    listings: list[ListingData] = []
    seen: set[str] = set()
    for card in cards:
        parsed = _parse_card(card)
        if parsed is None or parsed.id in seen:
            continue
        seen.add(parsed.id)
        listings.append(parsed)
    return listings


def _parse_card(card: Tag) -> ListingData | None:
    link = card.select_one('a[href*="/expose/"]')
    if link is None:
        return None
    href = str(link.get("href") or "")
    match = _EXPOSE_ID.search(href)
    if not match:
        return None
    listing_id = match.group(1)
    url = href if href.startswith("http") else f"{BASE_URL}{href}"

    test_id = str(card.get("data-testid") or "")
    if test_id.startswith("classified-card-mfe-"):
        short_id = test_id.removeprefix("classified-card-mfe-").lower()
        if short_id:
            listing_id = short_id

    price_text = _text(card, '[data-testid="cardmfe-price-testid"]')
    address = _text(card, '[data-testid="cardmfe-description-box-address"]')
    keyfacts = _text(card, '[data-testid="cardmfe-keyfacts-testid"]')
    description = _text(card, '[data-testid="cardmfe-description-box-text-test-id"]')

    rooms = None
    sqm = None
    if keyfacts:
        room_match = _ROOMS.search(keyfacts)
        if room_match:
            rooms = parse_number(room_match.group(1))
        sqm_match = _SQM.search(keyfacts)
        if sqm_match:
            sqm = parse_sqm(sqm_match.group(0))

    price = parse_amount(price_text or "")
    title = description or keyfacts or address or f"Wohnung {address or listing_id}"
    if address and address in title:
        title = title.split(address)[0].strip() or title

    image = card.select_one("img")
    image_url = None
    if image is not None:
        image_url = image.get("src") or image.get("data-src")

    return ListingData(
        id=listing_id,
        title=title[:200],
        price=price,
        size_sqm=sqm,
        rooms=rooms,
        location=address,
        url=url,
        image_url=image_url if isinstance(image_url, str) else None,
        source_platform="immowelt",
        description=description or "",
        raw_data={"keyfacts": keyfacts, "price_text": price_text},
    )


def _text(node: Tag, selector: str) -> str | None:
    found = node.select_one(selector)
    if found is None:
        return None
    text = " ".join(found.get_text(" ", strip=True).split())
    return text or None


async def _load_one(
    client: httpx.AsyncClient,
    listing: ListingData,
    semaphore: asyncio.Semaphore,
) -> None:
    async with semaphore:
        try:
            response = await client.get(listing.url)
            response.raise_for_status()
        except httpx.HTTPError as error:
            logger.warning(
                "Immowelt: страница %s недоступна (%s)", listing.id, error
            )
            return

    soup = BeautifulSoup(response.text, "html.parser")
    desc_node = soup.select_one('[data-testid*="description"]')
    if desc_node is not None:
        listing.description = " ".join(desc_node.get_text(" ", strip=True).split())

    blob = response.text
    warm = _WARM.search(blob)
    kalt = _KALT.search(blob)
    warm_price = parse_amount(warm.group(1)) if warm else None
    kalt_price = parse_amount(kalt.group(1)) if kalt else None
    if warm_price is not None:
        listing.price = warm_price
    elif kalt_price is not None:
        listing.price = kalt_price

    if listing.rooms is None:
        room_match = _ROOMS.search(blob[:8000])
        if room_match:
            listing.rooms = parse_number(room_match.group(1))
    if listing.size_sqm is None:
        sqm_match = _SQM.search(blob[:8000])
        if sqm_match:
            listing.size_sqm = parse_sqm(sqm_match.group(0))

    next_items = _parse_next_data(response.text)
    if next_items and next_items[0].description and not listing.description:
        listing.description = next_items[0].description
