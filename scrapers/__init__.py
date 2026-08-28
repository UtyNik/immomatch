"""Пакет сборщиков объявлений."""

from scrapers.kleinanzeigen import (
    FOLLOWUP_SEARCH_PAGES,
    INITIAL_SEARCH_PAGES,
    ScraperError,
    fetch_kleinanzeigen_listings,
    fetch_listing_cards,
    load_listing_details,
)

__all__ = [
    "FOLLOWUP_SEARCH_PAGES",
    "INITIAL_SEARCH_PAGES",
    "fetch_kleinanzeigen_listings",
    "fetch_listing_cards",
    "load_listing_details",
    "ScraperError",
]
