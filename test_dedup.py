"""Локальная проверка дедупликации между площадками."""

from __future__ import annotations

import asyncio
import sys

import aiosqlite

from database.db import _CREATE_LISTINGS_TABLE
from services.deduplicator import (
    apartment_to_listing_data,
    is_duplicate_listing,
    listings_are_cross_platform_duplicates,
)
from services.listing_price import format_price_line
from services.parsers.base import ListingData


async def _seed(db: aiosqlite.Connection) -> None:
    await db.execute(_CREATE_LISTINGS_TABLE)
    await db.execute(
        """
        INSERT INTO listings (
            source, external_id, url, title, price, size_sqm, rooms, location
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "kleinanzeigen",
            "111",
            "https://example.com/ka/111",
            "2-Zimmer in Achern",
            860,
            60.0,
            2.0,
            "77734 Achern, Hauptstr. 1",
        ),
    )
    await db.commit()


def _immowelt_listing() -> ListingData:
    return ListingData(
        id="abc-immowelt",
        title="Helle 2-Zimmer-Wohnung Achern",
        price=855,
        size_sqm=59.5,
        rooms=2.0,
        location="Achern | 62 m²",
        url="https://immowelt.de/expose/abc",
        image_url=None,
        source_platform="immowelt",
        raw_data={
            "rent_breakdown": {"warm": 855, "kalt": 700, "neben": 155},
            "price_kind": "warm",
        },
    )


async def main() -> None:
    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        await _seed(db)

        duplicate = _immowelt_listing()
        assert await is_duplicate_listing(duplicate, db=db), "expected duplicate"

        different_price = ListingData(
            id="other",
            title="Other flat",
            price=1200,
            size_sqm=60.0,
            rooms=2.0,
            location="Achern",
            url="https://example.com/x",
            image_url=None,
            source_platform="immowelt",
        )
        assert not await is_duplicate_listing(different_price, db=db)

        apartment = {
            "external_id": "abc",
            "source": "immowelt",
            "price": 860,
            "price_kind": "warm",
            "rooms": 2.0,
            "sqm": 60.0,
            "address": "Achern",
            "link": "https://example.com",
            "raw_data": {"rent_breakdown": {"warm": 860, "kalt": 700, "neben": 160}},
        }
        price_line = format_price_line("ru", apartment)
        assert "860" in price_line and "Warmmiete" in price_line
        assert "700" in price_line and "Kalt" in price_line

        dto = apartment_to_listing_data(apartment)
        assert listings_are_cross_platform_duplicates(
            dto,
            {
                "source": "kleinanzeigen",
                "location": "77734 Achern",
                "rooms": 2.0,
                "size_sqm": 60.0,
                "price": 850,
            },
        )

    print("test_dedup: OK")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as error:
        print("test_dedup: FAILED", error, file=sys.stderr)
        raise SystemExit(1) from error
