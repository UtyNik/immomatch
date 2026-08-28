"""Случайные паузы и эвристики блокировок для HTTP-парсеров."""

from __future__ import annotations

import asyncio
import random
from typing import Final

DEFAULT_DELAY_MIN: Final[float] = 1.5
DEFAULT_DELAY_MAX: Final[float] = 3.5


async def polite_delay(
    *,
    min_sec: float = DEFAULT_DELAY_MIN,
    max_sec: float = DEFAULT_DELAY_MAX,
) -> None:
    """Случайная пауза перед запросом — снижает риск rate limit / CAPTCHA."""
    await asyncio.sleep(random.uniform(min_sec, max_sec))


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
