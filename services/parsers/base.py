"""Базовый контракт провайдеров объявлений и общая DTO."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ListingData:
    """Единая структура объявления для всех площадок."""

    id: str
    title: str
    price: int | None
    size_sqm: float | None
    rooms: float | None
    location: str | None
    url: str
    image_url: str | None
    source_platform: str
    raw_data: dict[str, Any] = field(default_factory=dict)
    description: str = ""


class BaseProvider(ABC):
    """Асинхронный провайдер объявлений одной площадки."""

    name: str

    @abstractmethod
    async def fetch_listings(self, search_criteria: dict[str, Any]) -> list[ListingData]:
        """Возвращает объявления по критериям поиска из анкеты пользователя."""

    async def load_details(self, listings: list[ListingData]) -> None:
        """Догружает описание и уточняет цену. По умолчанию — no-op."""


def listing_storage_id(source: str, external_id: str) -> str:
    """Ключ для seen_apartments: source + external_id без коллизий между сайтами."""
    return f"{source}:{external_id}"


def listing_to_legacy_dict(listing: ListingData) -> dict[str, Any]:
    """Преобразует DTO в словарь, который понимают фильтры, AI и карточка."""
    address = listing.location or ""
    distance_km = listing.raw_data.get("distance_km")
    return {
        "external_id": listing.id,
        "source": listing.source_platform,
        "storage_id": listing_storage_id(listing.source_platform, listing.id),
        "title": listing.title,
        "price": listing.price,
        "rooms": listing.rooms,
        "sqm": listing.size_sqm,
        "address": address,
        "distance_km": distance_km,
        "link": listing.url,
        "image_url": listing.image_url,
        "description": listing.description or "",
        "raw_data": listing.raw_data,
    }


def legacy_dict_storage_id(listing: dict[str, Any]) -> str:
    """storage_id из legacy-словаря (обратная совместимость)."""
    if listing.get("storage_id"):
        return str(listing["storage_id"])
    source = str(listing.get("source") or "kleinanzeigen")
    external_id = str(listing.get("external_id") or listing.get("id") or "")
    return listing_storage_id(source, external_id)
