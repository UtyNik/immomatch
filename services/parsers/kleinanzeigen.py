"""Провайдер Kleinanzeigen.de — обёртка над scrapers/kleinanzeigen.py."""

from __future__ import annotations

import logging
from typing import Any

from scrapers import ScraperError, fetch_listing_cards, load_listing_details
from services.parsers.base import BaseProvider, ListingData, listing_to_legacy_dict
from services.listing_time import parse_iso_timestamp

logger = logging.getLogger(__name__)


class KleinanzeigenProvider(BaseProvider):
    name = "kleinanzeigen"

    async def fetch_listings(self, search_criteria: dict[str, Any]) -> list[ListingData]:
        city = str(search_criteria.get("city_de") or search_criteria.get("city") or "")
        try:
            cards = await fetch_listing_cards(
                city,
                radius=int(search_criteria.get("radius") or 0),
                budget_max=search_criteria.get("budget_max"),
                rooms_min=search_criteria.get("rooms_min"),
                max_pages=int(search_criteria.get("max_pages") or 1),
            )
        except ScraperError:
            raise

        listings: list[ListingData] = []
        for card in cards:
            listing = ListingData(
                id=str(card.get("external_id") or ""),
                title=str(card.get("title") or ""),
                price=card.get("price"),
                size_sqm=card.get("sqm"),
                rooms=card.get("rooms"),
                location=card.get("address"),
                url=str(card.get("link") or ""),
                image_url=None,
                source_platform=self.name,
                description=str(card.get("description") or ""),
                published_at=parse_iso_timestamp(card.get("published_at")),
                raw_data={
                    "distance_km": card.get("distance_km"),
                    "price_kind": card.get("price_kind") or "kalt",
                    "landlord_contact": card.get("landlord_contact"),
                    "card": card,
                },
            )
            if listing.id and listing.url:
                listings.append(listing)
        logger.info("Kleinanzeigen: получено %d объявлений для %s", len(listings), city)
        return listings

    async def load_details(self, listings: list[ListingData]) -> None:
        if not listings:
            return
        legacy = [listing_to_legacy_dict(item) for item in listings]
        await load_listing_details(legacy)
        for listing, enriched in zip(listings, legacy, strict=True):
            listing.price = enriched.get("price")
            listing.rooms = enriched.get("rooms")
            listing.size_sqm = enriched.get("sqm")
            listing.location = enriched.get("address")
            listing.description = str(enriched.get("description") or "")
            listing.raw_data["distance_km"] = enriched.get("distance_km")
            listing.raw_data["price_kind"] = enriched.get("price_kind") or "kalt"
            breakdown = enriched.get("rent_breakdown")
            if breakdown:
                listing.raw_data["rent_breakdown"] = breakdown
            if enriched.get("landlord_contact"):
                listing.raw_data["landlord_contact"] = enriched.get("landlord_contact")
            if enriched.get("published_at"):
                listing.published_at = parse_iso_timestamp(enriched.get("published_at"))
