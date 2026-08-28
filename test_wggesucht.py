"""Локальная проверка парсера WG-Gesucht."""

from __future__ import annotations

import asyncio
import json
import sys

from services.parsers.wggesucht import WGGesuchtProvider


async def main() -> None:
    city = sys.argv[1] if len(sys.argv) > 1 else "Freiburg im Breisgau"
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 1200

    provider = WGGesuchtProvider()
    criteria = {
        "city_de": city,
        "budget_max": budget,
        "rooms_min": 2.0,
        "max_pages": 1,
    }

    print(f"WG-Gesucht search: city={city!r}, budget_max={budget}")
    listings = await provider.fetch_listings(criteria)
    print(f"Listings: {len(listings)}")

    if listings:
        await provider.load_details(listings[:3])

    for listing in listings[:5]:
        print(
            json.dumps(
                {
                    "id": listing.id,
                    "title": listing.title,
                    "price": listing.price,
                    "price_kind": listing.raw_data.get("price_kind"),
                    "sqm": listing.size_sqm,
                    "rooms": listing.rooms,
                    "location": listing.location,
                    "source": listing.raw_data.get("source", "html"),
                    "url": listing.url,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
