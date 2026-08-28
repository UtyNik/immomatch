"""Детектор временной аренды (Zwischenmiete / befristet)."""

from __future__ import annotations

import re
from typing import Any, Final

from services.parsers.base import ListingData

_TEMPORARY_LOG_LABEL: Final[str] = "Временная аренда / Zwischenmiete"

# Явно бессрочная аренда — не путать с «befristet» внутри «unbefristet».
_UNLIMITED_MARKERS = re.compile(
    r"\bunbefristet\b|\bunlimited\b|\bauf\s+unbestimmte\s+zeit\b",
    re.IGNORECASE,
)

_KEYWORD_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bzwischenmiete\b", re.I), "Zwischenmiete"),
    (re.compile(r"\bzwischenvermietung\b", re.I), "Zwischenvermietung"),
    (re.compile(r"\buntermiete\b", re.I), "Untermiete"),
    (re.compile(r"\bauf\s+zeit\b", re.I), "auf Zeit"),
    (re.compile(r"\bkurzzeit(?:miete|vermietung)?\b", re.I), "Kurzeitmiete"),
    (re.compile(r"\bzeitmiete\b", re.I), "Zeitmiete"),
    (re.compile(r"\bbefristet\b", re.I), "befristet"),
    (re.compile(r"\bпо\s+месяцам\b", re.I), "по месяцам"),
    (re.compile(r"\bограничен(?:ный|на)\s+срок\b", re.I), "ограниченный срок"),
    (re.compile(r"\bограничена\s+по\s+времени\b", re.I), "ограничена по времени"),
    (re.compile(r"\bbefristet\s+bis\b", re.I), "befristet bis"),
    (re.compile(r"\bnur\s+f(?:ü|u)r\s+\d+\s+monat", re.I), "nur für X Monate"),
    (re.compile(r"\bf(?:ü|u)r\s+\d+\s+monate\b", re.I), "für X Monate"),
    (re.compile(r"\bmindest(?:ens)?\s+\d+\s+monat", re.I), "минимальный срок (Monate)"),
    (re.compile(r"\bминималь(?:ный|ная)\s+срок\s+\d+\s+месяц", re.I), "минимальный срок 1 месяц"),
    (re.compile(r"\bab\s+\d{1,2}[\./]\d{1,2}[\./]\d{2,4}\s+bis\b", re.I), "ab … bis …"),
)

_WG_EMPTY_END_DATE: Final[frozenset[str]] = frozenset(
    {"", "0", "00.00.0000", "00.00.00", "null", "none"}
)


def _listing_text(listing: ListingData) -> str:
    parts = [listing.title or "", listing.description or ""]
    raw = listing.raw_data or {}
    for key in ("snippet", "summary", "rent_type_label"):
        value = raw.get(key)
        if value:
            parts.append(str(value))
    return " ".join(parts)


def _wg_api_payload(listing: ListingData) -> dict[str, Any]:
    raw = listing.raw_data or {}
    api = raw.get("api")
    if isinstance(api, dict):
        return api
    return raw


def _wg_rent_type_temporary(rent_type: object) -> bool:
    token = str(rent_type or "").strip()
    if not token:
        return False
    # WG: rent_types[] «0» в выдаче = unbefristet; «1» = befristet/Zwischenmiete.
    return token not in {"0", "0.0"}


def _wg_end_date_temporary(end_date: object) -> bool:
    token = str(end_date or "").strip()
    if token.casefold() in _WG_EMPTY_END_DATE:
        return False
    if re.fullmatch(r"0+\.0+\.0+", token):
        return False
    return bool(re.search(r"\d", token))


def _wg_metadata_reason(listing: ListingData) -> str | None:
    if listing.source_platform != "wggesucht":
        return None

    payload = _wg_api_payload(listing)
    rent_type = payload.get("rent_type")
    if rent_type is None:
        rent_type = listing.raw_data.get("rent_type")

    if _wg_rent_type_temporary(rent_type):
        return f"WG rent_type={rent_type}"

    for field in ("available_to_date", "available_until", "move_out_date", "end_date"):
        if _wg_end_date_temporary(payload.get(field)):
            return f"WG {field}={payload.get(field)!r}"

    return None


def _text_reason(text: str) -> str | None:
    if not text.strip():
        return None
    if _UNLIMITED_MARKERS.search(text):
        # «unbefristet» перекрывает общий паттерн «befristet» ниже.
        cleaned = _UNLIMITED_MARKERS.sub(" ", text)
    else:
        cleaned = text

    for pattern, label in _KEYWORD_PATTERNS:
        if pattern.search(cleaned):
            return label
    return None


def is_temporary_lease(listing: ListingData) -> tuple[bool, str]:
    """True и причина, если объявление — временная/befristet аренда."""
    meta = _wg_metadata_reason(listing)
    if meta is not None:
        return True, meta

    text = _listing_text(listing)
    keyword = _text_reason(text)
    if keyword is not None:
        return True, keyword

    return False, ""


def temporary_lease_reason(apartment: dict[str, Any]) -> str | None:
    """Причина отсева для legacy-словаря поиска, или None."""
    from services.deduplicator import apartment_to_listing_data

    is_temp, detail = is_temporary_lease(apartment_to_listing_data(apartment))
    if not is_temp:
        return None
    if detail:
        return f"{_TEMPORARY_LOG_LABEL} ({detail})"
    return _TEMPORARY_LOG_LABEL
