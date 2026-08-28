"""Парсинг и форматирование даты публикации объявлений (DE → relative ago)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

_BERLIN: Final = ZoneInfo("Europe/Berlin")

_ISO_FIELD = re.compile(
    r'"(?:creationDate|startDateTime|posterDate|activationDate)"\s*:\s*"([^"]+)"',
    re.IGNORECASE,
)
_ABSOLUTE_DE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b")
_RELATIVE = re.compile(
    r"(?i)\b(heute|gestern|vor\s+(\d+)\s+(minute[n]?|min\.?|stunde[n]?|std\.?|tag(?:en)?|woche[n]?))\b"
)


def parse_iso_timestamp(value: str | None) -> datetime | None:
    """Разбирает ISO-8601 из JSON/HTML Immowelt/Kleinanzeigen."""
    if not value or not str(value).strip():
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_BERLIN)
    return parsed


def parse_iso_from_html(html: str) -> datetime | None:
    """Ищет creationDate / startDateTime в теле HTML-страницы."""
    match = _ISO_FIELD.search(html)
    if not match:
        return None
    return parse_iso_timestamp(match.group(1))


def parse_german_listing_date(
    text: str | None,
    *,
    now: datetime | None = None,
) -> datetime | None:
    """Парсит «Heute», «vor 2 Stunden», «12.03.2026» из текста карточки."""
    if not text or not text.strip():
        return None

    reference = now or datetime.now(_BERLIN)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=_BERLIN)

    absolute = _ABSOLUTE_DE.search(text)
    if absolute:
        day, month, year = absolute.groups()
        year_int = int(year)
        if year_int < 100:
            year_int += 2000
        try:
            return datetime(year_int, int(month), int(day), 12, 0, tzinfo=_BERLIN)
        except ValueError:
            pass

    match = _RELATIVE.search(text)
    if not match:
        return None

    token = match.group(1).casefold()
    if token == "heute":
        start = reference.replace(hour=0, minute=0, second=0, microsecond=0)
        return start
    if token == "gestern":
        start = reference.replace(hour=0, minute=0, second=0, microsecond=0)
        return start - timedelta(days=1)

    amount = int(match.group(2))
    unit = match.group(3).casefold()
    if unit.startswith("min"):
        return reference - timedelta(minutes=amount)
    if unit.startswith("st") or unit.startswith("std"):
        return reference - timedelta(hours=amount)
    if unit.startswith("tag"):
        return reference - timedelta(days=amount)
    if unit.startswith("woch"):
        return reference - timedelta(weeks=amount)
    return None


def _plural_ru(count: int, one: str, few: str, many: str) -> str:
    n = abs(count) % 100
    n1 = n % 10
    if 11 <= n <= 19:
        return many
    if n1 == 1:
        return one
    if 2 <= n1 <= 4:
        return few
    return many


def _plural_ua(count: int, one: str, few: str, many: str) -> str:
    return _plural_ru(count, one, few, many)


def _plural_en(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def format_published_ago(
    published_at: datetime | str | None,
    lang: str,
    *,
    now: datetime | None = None,
) -> str | None:
    """«Опубликовано 2 часа назад» / «Published 2 hours ago»."""
    if published_at is None:
        return None
    if isinstance(published_at, str):
        parsed = parse_iso_timestamp(published_at)
        if parsed is None:
            parsed = parse_german_listing_date(published_at, now=now)
        if parsed is None:
            return None
        published_at = parsed

    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    moment = published_at.astimezone(UTC)
    delta = reference - moment
    if delta.total_seconds() < 0:
        return None

    seconds = int(delta.total_seconds())
    if seconds < 45:
        just_now = {"ua": "щойно", "ru": "только что", "en": "just now"}
        prefix = {"ua": "Опубліковано", "ru": "Опубликовано", "en": "Published"}
        return f"📅 {prefix.get(lang, prefix['ru'])} {just_now.get(lang, just_now['ru'])}"

    minutes = max(1, seconds // 60)
    if minutes < 60:
        return _format_unit(lang, minutes, "minute")

    hours = max(1, seconds // 3600)
    if hours < 24:
        return _format_unit(lang, hours, "hour")

    days = max(1, seconds // 86400)
    if days < 7:
        return _format_unit(lang, days, "day")

    weeks = max(1, days // 7)
    if weeks < 5:
        return _format_unit(lang, weeks, "week")

    local = moment.astimezone(_BERLIN)
    date_str = local.strftime("%d.%m.%Y")
    prefix = {"ua": "Опубліковано", "ru": "Опубликовано", "en": "Published"}
    return f"📅 {prefix.get(lang, prefix['ru'])} {date_str}"


def _format_unit(lang: str, count: int, unit: str) -> str:
    prefix = {"ua": "Опубліковано", "ru": "Опубликовано", "en": "Published"}
    head = prefix.get(lang, prefix["ru"])

    if lang == "ua":
        units = {
            "minute": _plural_ua(count, "хвилину", "хвилини", "хвилин"),
            "hour": _plural_ua(count, "годину", "години", "годин"),
            "day": _plural_ua(count, "день", "дні", "днів"),
            "week": _plural_ua(count, "тиждень", "тижні", "тижнів"),
        }
        return f"📅 {head} {count} {units[unit]} тому"

    if lang == "ru":
        units = {
            "minute": _plural_ru(count, "минуту", "минуты", "минут"),
            "hour": _plural_ru(count, "час", "часа", "часов"),
            "day": _plural_ru(count, "день", "дня", "дней"),
            "week": _plural_ru(count, "неделю", "недели", "недель"),
        }
        return f"📅 {head} {count} {units[unit]} назад"

    units = {
        "minute": _plural_en(count, "minute", "minutes"),
        "hour": _plural_en(count, "hour", "hours"),
        "day": _plural_en(count, "day", "days"),
        "week": _plural_en(count, "week", "weeks"),
    }
    return f"📅 {head} {count} {units[unit]} ago"
