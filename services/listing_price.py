"""Форматирование Kaltmiete / Warmmiete / Nebenkosten для карточки Telegram."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from texts import t


@dataclass(slots=True)
class RentBreakdown:
    warm: int | None = None
    kalt: int | None = None
    neben: int | None = None
    price_kind: str = "unknown"


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def extract_rent_breakdown(apartment: dict[str, Any]) -> RentBreakdown:
    """Собирает компоненты аренды из legacy-словаря объявления."""
    raw = apartment.get("raw_data") or {}
    price_kind = str(apartment.get("price_kind") or raw.get("price_kind") or "unknown")

    breakdown = raw.get("rent_breakdown")
    if isinstance(breakdown, dict):
        warm = _int_or_none(breakdown.get("warm"))
        kalt = _int_or_none(breakdown.get("kalt"))
        neben = _int_or_none(breakdown.get("neben"))
        if warm is not None or kalt is not None or neben is not None:
            return RentBreakdown(
                warm=warm,
                kalt=kalt,
                neben=neben,
                price_kind=price_kind,
            )

    api = raw.get("api")
    if isinstance(api, dict):
        warm = _int_or_none(api.get("total_costs"))
        kalt = _int_or_none(api.get("rent_costs"))
        neben = _int_or_none(api.get("utility_costs"))
        if warm is not None or kalt is not None:
            return RentBreakdown(
                warm=warm,
                kalt=kalt,
                neben=neben,
                price_kind="warm" if warm is not None else price_kind,
            )

    price = _int_or_none(apartment.get("price"))
    if price_kind == "warm":
        return RentBreakdown(warm=price, price_kind="warm")
    if price_kind == "kalt":
        return RentBreakdown(kalt=price, price_kind="kalt")
    return RentBreakdown(warm=price, price_kind=price_kind)


def format_price_line(lang: str, apartment: dict[str, Any]) -> str | None:
    """Строка цены для карточки: Warm / Kalt / NK с эмодзи."""
    breakdown = extract_rent_breakdown(apartment)
    warm = breakdown.warm
    kalt = breakdown.kalt
    neben = breakdown.neben

    if warm is not None and kalt is not None and neben is not None:
        return t(
            lang,
            "card_price_warm_detail",
            warm=warm,
            kalt=kalt,
            nk=neben,
        )

    if warm is not None and kalt is not None:
        nk = warm - kalt
        if nk > 0:
            return t(
                lang,
                "card_price_warm_detail",
                warm=warm,
                kalt=kalt,
                nk=nk,
            )
        return t(lang, "card_price_warm_only", warm=warm)

    if warm is not None:
        return t(lang, "card_price_warm_only", warm=warm)

    if kalt is not None:
        if neben is not None and neben > 0:
            return t(
                lang,
                "card_price_kalt_with_nk",
                kalt=kalt,
                nk=neben,
            )
        return t(lang, "card_price_kalt_only", kalt=kalt)

    price = _int_or_none(apartment.get("price"))
    if price is None:
        return None
    if breakdown.price_kind == "kalt":
        return t(lang, "card_price_kalt_only", kalt=price)
    return t(lang, "card_price_warm_only", warm=price)
