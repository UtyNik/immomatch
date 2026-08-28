"""Дедупликация объявлений между площадками по данным таблицы listings."""

from __future__ import annotations

import logging
import re
from typing import Any, Final

import aiosqlite

from database.db import fetch_recent_listings_for_dedup
from services.parsers.base import ListingData
from validators import primary_city_token

logger = logging.getLogger(__name__)

DEDUP_DAYS: Final[int] = 7
PRICE_TOLERANCE_EUR: Final[int] = 15
SQM_TOLERANCE: Final[float] = 1.0
_PLZ_CITY = re.compile(r"\b(\d{5})\s+([a-zäöüß\-]+)", re.IGNORECASE)


def normalize_location_city(location: str | None) -> str:
    """Город из location: нижний регистр, без PLZ и лишних частей."""
    if not location:
        return ""
    text = " ".join(str(location).split()).casefold()
    match = _PLZ_CITY.search(text)
    if match:
        return match.group(2).strip("-")
    for token in re.split(r"[\s,|/\-]+", text):
        cleaned = token.strip("-")
        if cleaned and not cleaned.isdigit() and len(cleaned) >= 3:
            return cleaned
    return primary_city_token(text)


def apartment_to_listing_data(apartment: dict[str, Any]) -> ListingData:
    """Legacy-словарь поиска → ListingData для дедупликации."""
    return ListingData(
        id=str(apartment.get("external_id") or ""),
        title=str(apartment.get("title") or ""),
        price=apartment.get("price"),
        size_sqm=apartment.get("sqm"),
        rooms=apartment.get("rooms"),
        location=str(apartment.get("address") or "") or None,
        url=str(apartment.get("link") or ""),
        image_url=apartment.get("image_url"),
        source_platform=str(apartment.get("source") or "kleinanzeigen"),
        description=str(apartment.get("description") or ""),
        raw_data=dict(apartment.get("raw_data") or {}),
    )


def _rooms_equal(left: object, right: object) -> bool:
    try:
        return abs(float(left) - float(right)) < 0.01
    except (TypeError, ValueError):
        return False


def _price_close(left: object, right: object) -> bool:
    try:
        return abs(int(left) - int(right)) <= PRICE_TOLERANCE_EUR
    except (TypeError, ValueError):
        return False


def _sqm_close(left: object, right: object) -> bool:
    try:
        return abs(float(left) - float(right)) <= SQM_TOLERANCE
    except (TypeError, ValueError):
        return False


def listings_are_cross_platform_duplicates(
    new_listing: ListingData,
    existing: dict[str, Any],
) -> bool:
    """True, если existing с другой площадки описывает ту же квартиру."""
    if str(existing.get("source") or "") == new_listing.source_platform:
        return False

    new_city = normalize_location_city(new_listing.location)
    old_city = normalize_location_city(existing.get("location"))
    if not new_city or not old_city or new_city != old_city:
        return False

    if new_listing.rooms is None or existing.get("rooms") is None:
        return False
    if not _rooms_equal(new_listing.rooms, existing.get("rooms")):
        return False

    if new_listing.size_sqm is None or existing.get("size_sqm") is None:
        return False
    if not _sqm_close(new_listing.size_sqm, existing.get("size_sqm")):
        return False

    if new_listing.price is None or existing.get("price") is None:
        return False
    if not _price_close(new_listing.price, existing.get("price")):
        return False

    return True


async def is_duplicate_listing(
    new_listing: ListingData,
    db: aiosqlite.Connection | None = None,
) -> bool:
    """Ищет дубликат на другой площадке среди listings за последние 7 дней."""
    if db is not None:
        recent = await fetch_recent_listings_for_dedup(
            exclude_source=new_listing.source_platform,
            days=DEDUP_DAYS,
            db=db,
        )
    else:
        recent = await fetch_recent_listings_for_dedup(
            exclude_source=new_listing.source_platform,
            days=DEDUP_DAYS,
        )

    for existing in recent:
        if listings_are_cross_platform_duplicates(new_listing, existing):
            logger.info(
                "Дубликат найден на %s, пропуск",
                new_listing.source_platform,
            )
            return True
    return False
