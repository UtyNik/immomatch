"""Сбор объявлений о съёме квартир с kleinanzeigen.de."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Final

import httpx
from bs4 import BeautifulSoup, Tag

from services.alerts import alert_blocked_html, alert_http_status, alert_parse_failure
from services.http_politeness import detect_block_reason, polite_delay_for
from services.listing_time import (
    parse_german_listing_date,
    parse_iso_from_html,
    parse_iso_timestamp,
)
from services.salutation import parse_kleinanzeigen_contact
from validators import (
    infer_price_kind,
    parse_amount,
    parse_listing_distance_km,
    parse_number,
    parse_search_radius,
    parse_sqm,
    primary_city_token,
)

logger = logging.getLogger(__name__)

BASE_URL: Final[str] = "https://www.kleinanzeigen.de"
# c203 — рубрика «Mietwohnungen», k0 — без дополнительных фильтров.
# Umkreis клеится к коду локации: k0c203l{id}r10. Сегмент /r10/ сайт
# читает как название места «r10», а не как радиус.
LOCATION_SUGGEST_URL: Final[str] = f"{BASE_URL}/s-ort-empfehlungen.json"

# Без узнаваемого User-Agent сайт отдаёт заглушку вместо списка объявлений.
# Chrome 124 слишком старый — после пачки автопоисков Kleinanzeigen отвечает 403.
_CHROME_UA: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HEADERS: Final[dict[str, str]] = {
    "User-Agent": _CHROME_UA,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": f"{BASE_URL}/",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}
_JSON_HEADERS: Final[dict[str, str]] = {
    "User-Agent": _CHROME_UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Referer": f"{BASE_URL}/",
}

REQUEST_TIMEOUT: Final[float] = 20.0
# Сколько объявлений догружать со страниц деталей: полное описание есть только там.
DEFAULT_LIMIT: Final[int] = 10
# Первый поиск по новой анкете листает выдачу; дальше — только первая страница.
FOLLOWUP_SEARCH_PAGES: Final[int] = 1
INITIAL_SEARCH_PAGES: Final[int] = 3
_PAGE_PAUSE_SEC: Final[float] = 1.0
# Одновременных запросов к сайту. Больше — риск получить 403/429.
MAX_CONCURRENCY: Final[int] = 2
_RETRY_STATUSES: Final[frozenset[int]] = frozenset({403, 429, 503})
_MAX_RETRIES: Final[int] = 3
_BLOCK_COOLDOWN_SEC: Final[float] = 15 * 60
# Cookies листинга нельзя смешивать с запросом подсказок города.
_LISTING_COOKIES = httpx.Cookies()
_blocked_until: float = 0.0
_session_warmed: bool = False

_UMLAUTS: Final[dict[str, str]] = {
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "Ä": "ae", "Ö": "oe", "Ü": "ue",
}
_ROOMS_IN_TITLE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:-\s*)?(?:Zimmer|Zi\.?)\b",
    re.IGNORECASE,
)
_SQM_IN_TEXT = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:m[²2]|qm)\b", re.IGNORECASE)
_LOCATION_IN_PATH = re.compile(r"c203l(\d+)", re.IGNORECASE)
# Суммы из текста: «Kaltmiete: 690,- €», «KM 790€», «Nebenkosten-VZ 190€».
_EURO_AMOUNT = r"(\d{2,5}(?:[.,]\d{3})?(?:[.,]\d{1,2})?)"
_WARM_IN_TEXT = re.compile(
    rf"(?:warmmiete|warm[\s\-]?miete|\bwm\b)\s*[:.]?\s*{_EURO_AMOUNT}",
    re.IGNORECASE,
)
_KALT_IN_TEXT = re.compile(
    rf"(?:kaltmiete|kalt[\s\-]?miete|\bkm\b)\s*[:.]?\s*{_EURO_AMOUNT}",
    re.IGNORECASE,
)
_NK_IN_TEXT = re.compile(
    rf"(?:nebenkosten(?:\s*[-/]?\s*vz)?|betriebskosten|\bnk\b)\s*[:.]?\s*{_EURO_AMOUNT}",
    re.IGNORECASE,
)
# Успешные id городов живут до перезапуска: автопоиск ходит сюда каждые N минут.
_LOCATION_IDS: dict[str, str] = {}


class ScraperError(RuntimeError):
    """Объявления получить не удалось: сеть, блокировка или изменившаяся вёрстка."""


def _seconds_blocked() -> int:
    """Сколько секунд ещё действует пауза после 403, или 0."""
    remaining = _blocked_until - time.monotonic()
    return max(0, int(remaining))


async def _mark_blocked(*, status_code: int = 403, url: str | None = None) -> None:
    """После серии 403/429 не дёргаем сайт каждые 10 минут — блок только усилится."""
    global _blocked_until, _session_warmed
    _blocked_until = time.monotonic() + _BLOCK_COOLDOWN_SEC
    _session_warmed = False
    logger.warning(
        "Kleinanzeigen ответил %s — пауза %.0f мин",
        status_code,
        _BLOCK_COOLDOWN_SEC / 60,
    )
    await alert_http_status("kleinanzeigen", status_code, url=url)


def _raise_if_blocked() -> None:
    remaining = _seconds_blocked()
    if remaining:
        raise ScraperError(
            f"Kleinanzeigen временно блокирует запросы, пауза ещё {remaining} с"
        )


async def _warmup(client: httpx.AsyncClient, *, force: bool = False) -> None:
    """Главная страница даёт cookies; без них поисковый URL часто отвечает 403."""
    global _session_warmed
    if _session_warmed and not force:
        return
    try:
        await polite_delay_for("kleinanzeigen")
        response = await client.get(BASE_URL)
        _session_warmed = response.status_code < 400
        logger.info(
            "Kleinanzeigen: главная %s, cookies %d",
            response.status_code,
            len(client.cookies),
        )
    except httpx.HTTPError:
        logger.warning("Kleinanzeigen: не удалось открыть главную", exc_info=True)
        _session_warmed = False


@asynccontextmanager
async def _listing_client() -> AsyncIterator[httpx.AsyncClient]:
    """Клиент списка/карточек с общей cookie-банкой."""
    async with httpx.AsyncClient(
        headers=HEADERS,
        cookies=_LISTING_COOKIES,
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    ) as client:
        yield client


def city_to_slug(city: str) -> str:
    """Превращает название города в кусок URL: «München» -> «muenchen»."""
    slug = city.strip().lower()
    for source, replacement in _UMLAUTS.items():
        slug = slug.replace(source.lower(), replacement)
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _digits_location_id(value: object) -> str | None:
    """Kleinanzeigen отдаёт id как 8940, '8940' или '_8940'."""
    raw = str(value or "").strip().lstrip("_lL")
    return raw if raw.isdigit() else None


def location_id_from_suggestions(payload: object, city: str) -> str | None:
    """Выбирает location id из JSON автодополнения Kleinanzeigen."""
    needle = city.strip().casefold()
    pairs: list[tuple[str, str]] = []

    def add(name: object, loc_id: object) -> None:
        text = str(name or "").strip()
        parsed = _digits_location_id(loc_id)
        # _0 = вся Германия, для Umkreis города не годится.
        if text and parsed and parsed != "0":
            pairs.append((text, parsed))

    if isinstance(payload, dict):
        nested = payload.get("data")
        if isinstance(nested, list):
            for item in nested:
                if isinstance(item, dict):
                    add(
                        item.get("n")
                        or item.get("name")
                        or item.get("label")
                        or item.get("key"),
                        item.get("id") or item.get("value") or item.get("lid"),
                    )
        else:
            # Живой ответ: {"_9027": "Offenburg - Baden-Württemberg"}.
            # Старые зеркала могли отдавать наоборот: {"Offenburg": "_9027"}.
            id_like_keys = sum(
                1 for key in payload if _digits_location_id(key)
            )
            id_like_values = sum(
                1
                for value in payload.values()
                if not isinstance(value, (dict, list))
                and _digits_location_id(value)
            )
            if id_like_keys >= id_like_values:
                for loc_id, name in payload.items():
                    if isinstance(name, dict):
                        add(
                            name.get("n") or name.get("name") or loc_id,
                            name.get("id") or loc_id,
                        )
                    else:
                        add(name, loc_id)
            else:
                for name, loc_id in payload.items():
                    if isinstance(loc_id, dict):
                        add(name, loc_id.get("id") or loc_id.get("value"))
                    else:
                        add(name, loc_id)
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                add(
                    item.get("n") or item.get("name") or item.get("label"),
                    item.get("id") or item.get("value"),
                )

    if not pairs:
        return None
    needles: list[str] = []
    for candidate in (needle, primary_city_token(city)):
        if candidate and candidate not in needles:
            needles.append(candidate)
    for item_needle in needles:
        exact = [item for item in pairs if item[0].casefold() == item_needle]
        if exact:
            return exact[0][1]
    for item_needle in needles:
        starts = [item for item in pairs if item[0].casefold().startswith(item_needle)]
        if starts:
            return starts[0][1]
    for item_needle in needles:
        contains = [item for item in pairs if item_needle in item[0].casefold()]
        if contains:
            return contains[0][1]
    return pairs[0][1]


def location_id_from_html(html: str, city: str = "") -> str | None:
    """Достаёт l-код из ссылок вида /lahr/k0c203l8035, только для этого города."""
    slug = city_to_slug(city) if city else ""
    if not slug:
        return None
    tagged = re.search(
        rf"{re.escape(slug)}/[^\"'\s<>]*c203l(\d+)", html, re.IGNORECASE
    )
    return tagged.group(1) if tagged else None


def _rooms_filter_value(rooms: object) -> str | None:
    """Значение фильтра Zimmer для URL: 1.5 или 2, без запятой."""
    try:
        value = float(rooms)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    if value.is_integer():
        return str(int(value))
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return text or None


def _budget_filter_value(budget: object) -> int | None:
    """Верхняя граница цены для Kleinanzeigen (там это Kaltmiete)."""
    try:
        amount = int(budget)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def build_search_url(
    city: str,
    radius: int = 0,
    location_id: str | None = None,
    *,
    budget_max: int | float | None = None,
    rooms_min: float | None = None,
    page: int = 1,
) -> str:
    """URL списка квартир: город, Umkreis, опционально цена и минимум комнат.

    Фильтры нельзя ставить перед k0c203: сегмент /preis::1200/k0c203 сайт
    читает как поиск по слову «preis::1200». Рабочий вид:
    /preis::1200/c203l{id}r20+wohnung_mieten.zimmer_d:1.5,
    Страница 2+: /preis::1200/seite:2/c203l{id}r20
    """
    slug = city_to_slug(primary_city_token(city) or city) or city_to_slug(city)
    if not slug:
        raise ScraperError(f"Не удалось построить URL для города {city!r}")
    km = parse_search_radius(radius)
    loc = _digits_location_id(location_id)
    budget = _budget_filter_value(budget_max)
    rooms = _rooms_filter_value(rooms_min)

    path: list[str] = [BASE_URL, "s-wohnung-mieten", slug]
    # Цена/комнаты без l-кода локации дают выдачу по всей Германии.
    if budget is not None and loc:
        path.append(f"preis::{budget}")
    if page > 1:
        path.append(f"seite:{page}")

    if loc and km > 0:
        category = f"c203l{loc}r{km}"
    elif loc:
        category = f"c203l{loc}"
    else:
        category = "k0c203"

    url = "/".join(path) + f"/{category}"
    if rooms and loc:
        url += f"+wohnung_mieten.zimmer_d:{rooms},"
    return url


def _amount_from_label(text: str, *labels: str) -> int | None:
    """«Nebenkosten 190 €» → 190, если строка начинается с одной из меток."""
    folded = text.casefold()
    for label in labels:
        if folded.startswith(label):
            return parse_amount(text[len(label) :].strip())
    return None


def _first_amount(pattern: re.Pattern[str], text: str) -> int | None:
    """Первая сумма по шаблону в тексте объявления."""
    match = pattern.search(text)
    return parse_amount(match.group(1)) if match else None


def resolve_warm_rent(
    listed: int | None,
    warm: int | None,
    kalt: int | None,
    neben: int | None,
) -> int | None:
    """Тёплая аренда: явная Warmmiete или Kaltmiete + Nebenkosten.

    На kleinanzeigen.de в рубрике квартир крупная цена — это обычно Kaltmiete.
    Если рядом указаны Nebenkosten, а Warmmiete нет, их нужно сложить: иначе
    бюджет 800 € пропускает квартиру за 690 + 215 NK.
    """
    if warm is not None:
        return warm
    base = kalt if kalt is not None else listed
    if base is not None and neben is not None:
        return base + neben
    return listed if listed is not None else base


def _text_or_none(node: Tag | None) -> str | None:
    """Текст узла без лишних пробелов."""
    if node is None:
        return None
    text = " ".join(node.get_text(" ", strip=True).split())
    return text or None


def _parse_card(card: Tag) -> dict[str, Any] | None:
    """Разбирает карточку из списка: id, заголовок, цену, адрес, ссылку."""
    external_id = card.get("data-adid")
    href = card.get("data-href")
    if not href:
        link = card.select_one("a[href*='/s-anzeige/']")
        href = link.get("href") if link is not None else None
    if not external_id or not href:
        logger.debug("Карточка без data-adid/data-href пропущена")
        return None

    title = _card_title(card)
    price_text = _card_price_text(card)
    address = _card_address(card)
    facts = _text_or_none(
        card.select_one(".simpletags, .aditem-main--bottom, p.font-strong.text-onSurfaceSubdued")
    )
    path = str(href)
    link = path if path.startswith("http") else f"{BASE_URL}{path}"

    published_at = _card_published_at(card)

    return {
        "external_id": str(external_id),
        "title": title or "",
        "price": parse_amount(price_text) if price_text else None,
        "price_kind": "kalt",
        "rooms": _rooms_from_title(title) or _rooms_from_title(facts),
        "sqm": _sqm_from_text(title) or _sqm_from_text(facts),
        "address": address,
        "distance_km": parse_listing_distance_km(address),
        "link": link,
        "description": "",
        "published_at": published_at.isoformat() if published_at else None,
    }


def _card_title(card: Tag) -> str | None:
    """Заголовок: старый .ellipsis или новая вёрстка с h3 / JSON-LD."""
    title = _text_or_none(card.select_one(".ellipsis, h3.line-clamp-2, h3"))
    if title:
        return title
    script = card.select_one("script[type='application/ld+json']")
    raw = script.string if script is not None else None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return str(payload.get("title") or "").strip() or None
    return None


def _card_price_text(card: Tag) -> str | None:
    """Цена со старой карточки или из блока «550 €» новой выдачи."""
    text = _text_or_none(
        card.select_one(
            ".aditem-main--middle--price-shipping--price, "
            "p.text-title3.font-strong.text-secondary"
        )
    )
    if text and "€" in text:
        return text
    for node in card.select("p"):
        text = _text_or_none(node)
        if text and "€" in text and len(text) < 24:
            return text
    return None


_ZIP_CITY = re.compile(r"^\d{5}\b")


def _card_address(card: Tag) -> str | None:
    """Адрес и пометка Umkreis: «77743 Neuried (9 km)»."""
    address = _text_or_none(card.select_one(".aditem-main--top--left"))
    if address:
        return address
    parts: list[str] = []
    for span in card.select("span"):
        text = _text_or_none(span)
        if not text:
            continue
        if _ZIP_CITY.match(text) or parse_listing_distance_km(text) is not None:
            if text not in parts:
                parts.append(text)
    return " ".join(parts) or None


def _card_published_at(card: Tag) -> datetime | None:
    """Дата публикации с карточки: time[datetime], JSON-LD или «Heute» / «vor 2 Std.»."""
    time_node = card.select_one("time[datetime]")
    if time_node is not None:
        raw = time_node.get("datetime")
        if isinstance(raw, str):
            parsed = parse_iso_timestamp(raw)
            if parsed is not None:
                return parsed

    script = card.select_one("script[type='application/ld+json']")
    raw_json = script.string if script is not None else None
    if raw_json:
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            for key in ("datePublished", "uploadDate"):
                stamp = payload.get(key)
                if isinstance(stamp, str):
                    parsed = parse_iso_timestamp(stamp)
                    if parsed is not None:
                        return parsed

    for selector in (
        ".aditem-main--top--right",
        "p.text-onSurfaceSubdued",
        "span.text-onSurfaceSubdued",
    ):
        text = _text_or_none(card.select_one(selector))
        parsed = parse_german_listing_date(text)
        if parsed is not None:
            return parsed

    card_text = card.get_text(" ", strip=True)
    return parse_german_listing_date(card_text)


def _rooms_from_title(title: str | None) -> float | None:
    """Достаёт количество комнат из заголовка вида «2 Zimmer Wohnung»."""
    if not title:
        return None
    match = _ROOMS_IN_TITLE.search(title)
    return parse_number(match.group(1)) if match else None


def _sqm_from_text(text: str | None) -> float | None:
    """Достаёт площадь из фрагмента вроде «74 m²» или «Wohnfläche 60 qm»."""
    if not text:
        return None
    match = _SQM_IN_TEXT.search(text)
    return parse_sqm(match.group(0)) if match else None

def _parse_details(html: str, listing: dict[str, Any]) -> None:
    """Дополняет объявление данными со страницы: описание, комнаты, Warmmiete.

    Изменяет переданный словарь на месте. Ошибки отдельных полей не мешают
    остальным: пропущенное поле остаётся None.
    """
    soup = BeautifulSoup(html, "html.parser")

    description = soup.select_one("#viewad-description-text")
    if description is not None:
        listing["description"] = description.get_text("\n", strip=True)
    else:
        logger.warning(
            "Объявление %s: не найден блок описания", listing["external_id"]
        )

    address = _text_or_none(soup.select_one("#viewad-locality"))
    if address:
        listing["address"] = address
        if listing.get("distance_km") is None:
            listing["distance_km"] = parse_listing_distance_km(address)

    # Список характеристик: «Zimmer 2», «Wohnfläche 60 m²», «Warmmiete 1.200 €».
    warm: int | None = None
    kalt: int | None = None
    neben: int | None = None
    for detail in soup.select(".addetailslist--detail"):
        text = detail.get_text(" ", strip=True)
        label = text.casefold()
        if listing.get("rooms") is None and label.startswith("zimmer"):
            listing["rooms"] = parse_number(text[len("zimmer"):].strip())
        elif label.startswith("wohnfläche") or label.startswith("wohnflaeche"):
            area = parse_sqm(text)
            if area is not None:
                listing["sqm"] = area
        elif label.startswith("warmmiete"):
            warm = _amount_from_label(text, "warmmiete")
        elif label.startswith("kaltmiete"):
            kalt = _amount_from_label(text, "kaltmiete")
        elif label.startswith("nebenkosten") or label.startswith("betriebskosten"):
            neben = _amount_from_label(text, "nebenkosten", "betriebskosten")

    description = str(listing.get("description") or "")
    if warm is None:
        warm = _first_amount(_WARM_IN_TEXT, description)
    if kalt is None:
        kalt = _first_amount(_KALT_IN_TEXT, description)
    if neben is None:
        neben = _first_amount(_NK_IN_TEXT, description)

    listed = listing.get("price")
    if listed is None:
        price_text = _text_or_none(soup.select_one("#viewad-price"))
        if price_text:
            listed = parse_amount(price_text)
    resolved = resolve_warm_rent(listed, warm, kalt, neben)
    if resolved is not None:
        listing["price"] = resolved
    listing["price_kind"] = infer_price_kind(
        warm=warm,
        kalt=kalt,
        neben=neben,
        default="kalt" if (kalt is not None or listed is not None) else "unknown",
    )
    listing["rent_breakdown"] = {
        "warm": warm,
        "kalt": kalt,
        "neben": neben,
    }

    contact_root = soup.select_one("#viewad-contact")
    contact_name = _text_or_none(soup.select_one(".userprofile-vip"))
    if contact_name:
        commercial = bool(
            contact_root
            and "Gewerblicher Nutzer" in contact_root.get_text(" ", strip=True)
        )
        listing["landlord_contact"] = parse_kleinanzeigen_contact(
            contact_name,
            commercial=commercial,
        )

    if listing.get("sqm") is None:
        listing["sqm"] = _sqm_from_text(listing.get("title"))

    if not listing.get("published_at"):
        stamp = parse_iso_from_html(html)
        if stamp is not None:
            listing["published_at"] = stamp.isoformat()


async def _load_details(
    client: httpx.AsyncClient,
    listing: dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> None:
    """Скачивает страницу объявления и дополняет словарь."""
    async with semaphore:
        try:
            await polite_delay_for("kleinanzeigen")
            response = await client.get(listing["link"])
            response.raise_for_status()
        except httpx.HTTPError as error:
            # Одно недоступное объявление не должно ронять весь поиск.
            logger.warning(
                "Объявление %s: страница недоступна (%s)",
                listing["external_id"],
                error,
            )
            return

    try:
        _parse_details(response.text, listing)
    except Exception:
        logger.exception(
            "Объявление %s: не удалось разобрать страницу", listing["external_id"]
        )


async def _get_search_html(client: httpx.AsyncClient, url: str) -> str:
    """Скачивает HTML страницы поиска или бросает ScraperError."""
    _raise_if_blocked()
    await _warmup(client)
    last_status: int | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            await polite_delay_for("kleinanzeigen")
            response = await client.get(url)
        except httpx.HTTPError as error:
            raise ScraperError(f"Сеть недоступна: {error}") from error
        if response.status_code in _RETRY_STATUSES:
            last_status = response.status_code
            logger.warning(
                "Kleinanzeigen %s на %s, попытка %d/%d",
                response.status_code,
                url,
                attempt,
                _MAX_RETRIES,
            )
            if attempt < _MAX_RETRIES:
                await _warmup(client, force=True)
                await asyncio.sleep(2 ** attempt)
            continue
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise ScraperError(
                f"Сайт ответил {error.response.status_code} на {url}"
            ) from error
        block = detect_block_reason(response.text)
        if block:
            await alert_blocked_html(
                "kleinanzeigen",
                response.text,
                context=f"{block} — {url}",
            )
            raise ScraperError(f"Kleinanzeigen: {block} на {url}")
        return response.text

    await _mark_blocked(status_code=last_status or 403, url=url)
    raise ScraperError(f"Сайт ответил {last_status} на {url}")


async def _lookup_location_id(city: str) -> str | None:
    """Код локации Kleinanzeigen. Без l-кода фильтры цены дают выдачу по всей Германии.

    Отдельный HTTP-клиент обязателен: если дергать подсказки тем же клиентом,
    что и список объявлений, сайт отдаёт карточки без заголовка и цены.
    """
    key = city.strip().casefold()
    if key in _LOCATION_IDS:
        return _LOCATION_IDS[key]
    loc_id: str | None = None
    try:
        async with httpx.AsyncClient(
            headers=_JSON_HEADERS, timeout=REQUEST_TIMEOUT, follow_redirects=True
        ) as client:
            await polite_delay_for("kleinanzeigen")
            response = await client.get(
                LOCATION_SUGGEST_URL, params={"query": city.strip()}
            )
            response.raise_for_status()
            loc_id = location_id_from_suggestions(response.json(), city)
    except Exception:
        logger.warning("Не удалось получить location id для %s", city, exc_info=True)
        loc_id = None
    if not loc_id:
        short = primary_city_token(city)
        if short and short != key:
            try:
                async with httpx.AsyncClient(
                    headers=_JSON_HEADERS, timeout=REQUEST_TIMEOUT, follow_redirects=True
                ) as client:
                    await polite_delay_for("kleinanzeigen")
                    response = await client.get(
                        LOCATION_SUGGEST_URL, params={"query": short}
                    )
                    response.raise_for_status()
                    loc_id = location_id_from_suggestions(response.json(), short)
            except Exception:
                logger.warning(
                    "Не удалось получить location id для %s", short, exc_info=True
                )
                loc_id = None
    if loc_id:
        _LOCATION_IDS[key] = loc_id
        logger.info("Kleinanzeigen: %s → location id %s", city, loc_id)
    else:
        logger.warning("Kleinanzeigen: нет location id для %s — город в URL без l-кода", city)
    return loc_id


def _listing_nodes(html: str) -> list[Tag]:
    """Карточки поиска: старая таблица aditem или новая article[data-adid]."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("#srchrslt-adtable article.aditem")
    if cards:
        return cards
    cards = soup.select("article.aditem")
    if cards:
        return cards
    return soup.select("article[data-adid]")


def _parse_listing_cards(html: str) -> list[dict[str, Any]]:
    """Разбирает все карточки со страницы поиска."""
    listings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in _listing_nodes(html):
        try:
            parsed = _parse_card(card)
        except Exception:
            logger.exception("Не удалось разобрать карточку объявления")
            continue
        if parsed is None:
            continue
        listing_id = parsed["external_id"]
        if listing_id in seen:
            continue
        seen.add(listing_id)
        listings.append(parsed)
    return listings


_SEITE_IN_HTML = re.compile(r"seite:(\d+)", re.IGNORECASE)


def _search_has_later_page(html: str, current_page: int) -> bool:
    """Есть ли в пагинации страница после текущей."""
    later = any(int(n) > current_page for n in _SEITE_IN_HTML.findall(html))
    if later:
        return True
    return current_page == 1 and "pagination-next" in html.casefold()


async def fetch_listing_cards(
    city: str,
    radius: int = 0,
    *,
    budget_max: int | float | None = None,
    rooms_min: float | None = None,
    max_pages: int = FOLLOWUP_SEARCH_PAGES,
) -> list[dict[str, Any]]:
    """Возвращает объявления с первых `max_pages` страниц поиска без описаний.

    В карточке уже есть цена, заголовок и ссылка — этого хватает, чтобы
    отсеять заведомо неподходящее до загрузки страниц.

    Вызывает ScraperError, если первую страницу получить не удалось.
    Сбой на второй или третьей отдаёт то, что уже собрано.
    """
    km = parse_search_radius(radius)
    pages = max(FOLLOWUP_SEARCH_PAGES, min(int(max_pages), INITIAL_SEARCH_PAGES))
    url_kwargs = {"budget_max": budget_max, "rooms_min": rooms_min}

    def make_url(location_id: str | None, radius_km: int, page: int = 1) -> str:
        return build_search_url(
            city, radius_km, location_id, page=page, **url_kwargs
        )

    listings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    url = ""
    html = ""

    async with _listing_client() as client:
        location_id = await _lookup_location_id(city)
        url = make_url(location_id, km)
        logger.info("Запрашиваю объявления: %s", url)
        html = await _get_search_html(client, url)

        if not _digits_location_id(location_id):
            location_id = location_id_from_html(
                html, primary_city_token(city) or city
            )
            if location_id:
                _LOCATION_IDS[city.strip().casefold()] = location_id
                url = make_url(location_id, km)
                logger.info("Повтор с location id: %s", url)
                html = await _get_search_html(client, url)

        for page in range(1, pages + 1):
            if page > 1:
                if not _search_has_later_page(html, page - 1):
                    break
                await asyncio.sleep(_PAGE_PAUSE_SEC)
                url = make_url(location_id, km, page)
                logger.info("Запрашиваю объявления: %s", url)
                try:
                    html = await _get_search_html(client, url)
                except ScraperError:
                    logger.warning(
                        "Страница %d поиска недоступна, отдаю %d карточек",
                        page,
                        len(listings),
                    )
                    break

            page_listings = _parse_listing_cards(html)
            if not page_listings:
                raw_count = len(_listing_nodes(html))
                if raw_count:
                    logger.warning(
                        "На странице %d узлов объявлений, ни один не разобрался",
                        raw_count,
                    )
                    if page == 1:
                        await alert_parse_failure(
                            "kleinanzeigen",
                            detail=f"узлов {raw_count}, разобрано 0",
                        )
                elif page == 1 and detect_block_reason(html):
                    await alert_blocked_html(
                        "kleinanzeigen",
                        html,
                        context="пустая выдача на первой странице",
                    )
                elif page == 1 and km > 0:
                    fallback = make_url(location_id, 0)
                    if fallback != url:
                        logger.warning(
                            "Радиус %s км не дал карточек, ищу только город %s",
                            km,
                            city,
                        )
                        html = await _get_search_html(client, fallback)
                        km = 0
                        page_listings = _parse_listing_cards(html)

            added = 0
            for item in page_listings:
                listing_id = str(item.get("external_id") or "")
                if not listing_id or listing_id in seen_ids:
                    continue
                seen_ids.add(listing_id)
                listings.append(item)
                added += 1
            logger.info(
                "Страница %d: +%d карточек (всего %d)", page, added, len(listings)
            )
            if added == 0:
                break

    if not listings:
        logger.warning(
            "Объявления не найдены на %s (получено %d символов)",
            url,
            len(html),
        )
        return []

    logger.info("Найдено объявлений: %d (город %s, до %d стр.)", len(listings), city, pages)
    return listings


async def load_listing_details(listings: Sequence[dict[str, Any]]) -> None:
    """Догружает описание, число комнат и Warmmiete для переданных объявлений.

    Меняет словари на месте. Каждая страница — отдельный запрос, поэтому
    вызывать стоит только для тех объявлений, которые дошли до оценки.
    """
    if not listings:
        return

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    async with _listing_client() as client:
        await _warmup(client)
        await asyncio.gather(
            *(_load_details(client, listing, semaphore) for listing in listings)
        )


async def fetch_kleinanzeigen_listings(
    city: str,
    limit: int = DEFAULT_LIMIT,
    with_details: bool = True,
    radius: int = 0,
) -> list[dict[str, Any]]:
    """Объявления города вместе с описаниями первых `limit` штук.

    Удобная обёртка для разовых проверок. В поиске используется пара
    `fetch_listing_cards` + `load_listing_details`: она позволяет отсеять
    лишнее до того, как тратить запросы на страницы объявлений.
    """
    listings = await fetch_listing_cards(
        city, radius=radius, budget_max=None, rooms_min=None
    )
    if with_details:
        await load_listing_details(listings[:limit])
    return listings
