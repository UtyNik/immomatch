"""Оркестратор поиска по нескольким площадкам."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final

from scrapers import ScraperError
from config import get_settings
from services.parsers.base import (
    BaseProvider,
    ListingData,
    listing_to_legacy_dict,
)
from services.listing_time import parse_iso_timestamp
from services.parsers.immowelt import ImmoweltProvider
from services.parsers.kleinanzeigen import KleinanzeigenProvider
from services.parsers.wggesucht import WGGesuchtProvider

logger = logging.getLogger(__name__)

_PROVIDER_TIMEOUT: Final[float] = 90.0


def _default_providers() -> list[BaseProvider]:
    providers: list[BaseProvider] = [
        KleinanzeigenProvider(),
        ImmoweltProvider(),
    ]
    if get_settings().enable_wg_gesucht:
        providers.append(WGGesuchtProvider())
    else:
        logger.info("WG-Gesucht отключён (ENABLE_WG_GESUCHT=false)")
    return providers


class SearchOrchestrator:
    """Запрашивает объявления у всех активных провайдеров и объединяет результат."""

    def __init__(self, providers: list[BaseProvider] | None = None) -> None:
        self.providers = providers or _default_providers()
        self._by_name = {provider.name: provider for provider in self.providers}

    async def fetch_all(
        self, search_criteria: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Параллельно опрашивает провайдеры. Сбой одного не ломает остальных."""
        tasks = [
            self._fetch_provider(provider, search_criteria)
            for provider in self.providers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        merged: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        errors: list[str] = []
        for provider, result in zip(self.providers, results, strict=True):
            if isinstance(result, Exception):
                message = f"{provider.name}: {result}"
                errors.append(message)
                logger.error(
                    "Провайдер %s: сбой fetch_listings — %s",
                    provider.name,
                    result,
                )
                continue
            for listing in result:
                legacy = listing_to_legacy_dict(listing)
                key = legacy["storage_id"]
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                merged.append(legacy)

        logger.info(
            "Оркестратор: собрано %d объявлений из %d провайдеров",
            len(merged),
            len(self.providers),
        )
        return merged, errors

    async def load_details(self, listings: list[dict[str, Any]]) -> None:
        """Догружает страницы объявлений у соответствующих провайдеров."""
        grouped: dict[str, list[ListingData]] = {}
        for item in listings:
            dto = _legacy_to_listing_data(item)
            grouped.setdefault(dto.source_platform, []).append(dto)

        tasks: list[asyncio.Task[None]] = []
        for name, batch in grouped.items():
            provider = self._by_name.get(name)
            if provider is None:
                logger.warning("Нет провайдера для source=%s, пропуск details", name)
                continue
            tasks.append(asyncio.create_task(provider.load_details(batch)))

        if not tasks:
            return

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for provider_name, result in zip(grouped.keys(), results, strict=True):
            if isinstance(result, Exception):
                logger.error(
                    "Провайдер %s: сбой load_details — %s",
                    provider_name,
                    result,
                )

        for item in listings:
            source = str(item.get("source") or "kleinanzeigen")
            external_id = str(item.get("external_id") or "")
            for dto in grouped.get(source, []):
                if dto.id == external_id:
                    enriched = listing_to_legacy_dict(dto)
                    item.update(enriched)
                    break

    async def _fetch_provider(
        self,
        provider: BaseProvider,
        search_criteria: dict[str, Any],
    ) -> list[ListingData]:
        try:
            return await asyncio.wait_for(
                provider.fetch_listings(search_criteria),
                timeout=_PROVIDER_TIMEOUT,
            )
        except TimeoutError as error:
            raise RuntimeError(
                f"{provider.name}: таймаут {_PROVIDER_TIMEOUT}s"
            ) from error
        except ScraperError as error:
            raise RuntimeError(f"{provider.name}: {error}") from error


def _legacy_to_listing_data(item: dict[str, Any]) -> ListingData:
    published = item.get("published_at")
    published_at = (
        parse_iso_timestamp(str(published))
        if published
        else None
    )
    return ListingData(
        id=str(item.get("external_id") or ""),
        title=str(item.get("title") or ""),
        price=item.get("price"),
        size_sqm=item.get("sqm"),
        rooms=item.get("rooms"),
        location=item.get("address"),
        url=str(item.get("link") or ""),
        image_url=item.get("image_url"),
        source_platform=str(item.get("source") or "kleinanzeigen"),
        description=str(item.get("description") or ""),
        published_at=published_at,
        raw_data=dict(item.get("raw_data") or {}),
    )


_default_orchestrator: SearchOrchestrator | None = None


def get_search_orchestrator() -> SearchOrchestrator:
    """Singleton оркестратора для поиска и автопоиска."""
    global _default_orchestrator
    if _default_orchestrator is None:
        _default_orchestrator = SearchOrchestrator()
    return _default_orchestrator
