"""Провайдеры объявлений с разных площадок."""

from services.parsers.base import BaseProvider, ListingData, listing_to_legacy_dict
from services.parsers.kleinanzeigen import KleinanzeigenProvider
from services.parsers.immowelt import ImmoweltProvider
from services.parsers.wggesucht import WGGesuchtProvider

__all__ = [
    "BaseProvider",
    "ListingData",
    "KleinanzeigenProvider",
    "ImmoweltProvider",
    "WGGesuchtProvider",
    "listing_to_legacy_dict",
]
