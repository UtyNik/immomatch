"""Случайные паузы и эвристики блокировок для HTTP-парсеров."""

from __future__ import annotations

import asyncio
import random
from typing import Final

DEFAULT_DELAY_MIN: Final[float] = 1.5
DEFAULT_DELAY_MAX: Final[float] = 3.5

# Пауза перед запросом (сек): min, max.
# Kleinanzeigen — алертов нет, базовый интервал.
# Immowelt и WG-Gesucht — бывают CAPTCHA/403; у Immowelt чуть короче, у WG дольше всего.
PLATFORM_DELAYS: Final[dict[str, tuple[float, float]]] = {
    "kleinanzeigen": (1.5, 3.5),
    "immowelt": (2.5, 4.5),
    "wggesucht": (3.5, 7.0),
}

# Nominatim / Overpass — не целевые площадки, умеренная пауза.
_GEO_DELAY: Final[tuple[float, float]] = (1.0, 2.0)


async def polite_delay(
    *,
    min_sec: float = DEFAULT_DELAY_MIN,
    max_sec: float = DEFAULT_DELAY_MAX,
) -> None:
    """Случайная пауза перед запросом — снижает риск rate limit / CAPTCHA."""
    await asyncio.sleep(random.uniform(min_sec, max_sec))


async def polite_delay_for(platform: str) -> None:
    """Пауза с профилем задержки для конкретной площадки."""
    bounds = PLATFORM_DELAYS.get(platform.casefold())
    if bounds is None:
        await polite_delay()
        return
    await polite_delay(min_sec=bounds[0], max_sec=bounds[1])


async def polite_delay_geo() -> None:
    """Пауза перед запросами к геосервисам (Nominatim, Overpass)."""
    await polite_delay(min_sec=_GEO_DELAY[0], max_sec=_GEO_DELAY[1])


def detect_block_reason(html: str) -> str | None:
    """CAPTCHA / Cloudflare в HTML, или None если страница выглядит нормальной."""
    if not html:
        return "пустой ответ"
    low = html.casefold()
    if "captcha" in low or "cf-browser-verification" in low or "datadome" in low:
        return "CAPTCHA"
    if "access denied" in low or "zugang verweigert" in low:
        return "403 Forbidden"
    return None
