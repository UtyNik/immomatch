"""Провайдер WG-Gesucht.de: HTML-карточки, sitemap+API и JSON /api/offers."""

from __future__ import annotations

import asyncio
import gzip
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup, Tag

from services.alerts import alert_blocked_html, alert_http_status
from services.http_politeness import detect_block_reason, polite_delay
from services.listing_time import parse_german_listing_date
from services.parsers.base import BaseProvider, ListingData
from validators import infer_price_kind, parse_amount, parse_first_amount, parse_number, parse_sqm

logger = logging.getLogger(__name__)

BASE_URL: Final[str] = "https://www.wg-gesucht.de"
SITEMAP_URL: Final[str] = (
    f"{BASE_URL}/sitemaps/offer_detail_views/offer_details_DE.xml.gz"
)
REQUEST_TIMEOUT: Final[float] = 20.0
DETAIL_TIMEOUT: Final[float] = 12.0
SITEMAP_TIMEOUT: Final[float] = 45.0
MAX_CONCURRENCY: Final[int] = 3
SITEMAP_CACHE_TTL: Final[float] = 3600.0
MAX_SITEMAP_SCAN: Final[int] = 400
MAX_SITEMAP_API_CALLS: Final[int] = 60
_RADIUS_EXPAND_THRESHOLD: Final[int] = 8
_MAX_RADIUS_CITIES: Final[int] = 12
_NOMINATIM_URL: Final[str] = "https://nominatim.openstreetmap.org/search"
_OVERPASS_URL: Final[str] = "https://overpass-api.de/api/interpreter"
_GEOCODE_UA: Final[str] = "ImmomatchBot/1.0 (wg-gesucht radius lookup)"

_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Referer": f"{BASE_URL}/",
}

_API_HEADERS: dict[str, str] = {
    "User-Agent": _HEADERS["User-Agent"],
    "Accept": "application/json",
    "Accept-Language": "de-DE,de;q=0.9",
    "X-Client-Id": "wg_mobile_app",
}

_UMLAUTS: dict[str, str] = {
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "ß": "ss",
    "Ä": "Ae",
    "Ö": "Oe",
    "Ü": "Ue",
}

_CATEGORY_SLUGS: dict[str, str] = {
    "0": "wg-zimmer",
    "1": "1-zimmer-wohnungen",
    "2": "wohnungen",
    "3": "haeuser",
}

_SITEMAP_LINE = re.compile(
    r"https://www\.wg-gesucht\.de/"
    r"(?P<category>wg-zimmer|1-zimmer-wohnungen|wohnungen|haeuser)"
    r"-in-(?P<location>[^<]+?)\.(?P<offer_id>\d+)\.html"
)

_sitemap_cache: tuple[float, str] | None = None
_sitemap_lock = asyncio.Lock()
_geocode_cache: dict[str, tuple[float, float] | None] = {}
_nearby_names_cache: dict[tuple[str, int], list[str]] = {}

_ROOMS_IN_TEXT = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*[-\s]?Zimmer|Einzimmer",
    re.IGNORECASE,
)


@dataclass(slots=True)
class _CityInfo:
    city_id: str
    city_name: str
    slug: str
    federated_state_id: str = ""


class WGGesuchtProvider(BaseProvider):
    name = "wggesucht"

    async def fetch_listings(self, search_criteria: dict[str, Any]) -> list[ListingData]:
        city = str(search_criteria.get("city_de") or search_criteria.get("city") or "")
        max_pages = max(1, int(search_criteria.get("max_pages") or 1))
        budget_max = search_criteria.get("budget_max")
        rooms_min = search_criteria.get("rooms_min")
        radius = int(search_criteria.get("radius") or 0)

        listings: list[ListingData] = []
        seen_ids: set[str] = set()
        html_blocked = False

        async with httpx.AsyncClient(
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        ) as client:
            city_info = await _resolve_city(client, city)
            if city_info is None:
                raise RuntimeError(f"WG-Gesucht: город не найден — {city!r}")

            search_cities = [city_info]
            categories = _categories_for_search(rooms_min)

            html_blocked = await _fetch_html_listings(
                client,
                search_cities,
                categories=categories,
                max_pages=max_pages,
                budget_max=budget_max,
                seen_ids=seen_ids,
                listings=listings,
            )

            if radius > 0 and len(listings) < _RADIUS_EXPAND_THRESHOLD:
                extra_cities = await _resolve_radius_cities(client, city_info, radius)
                extra_cities = [
                    item
                    for item in extra_cities
                    if item.city_id != city_info.city_id
                    and item.city_id not in {c.city_id for c in search_cities}
                ]
                if extra_cities:
                    logger.info(
                        "WG-Gesucht: мало объявлений в %s (%d) — расширяю радиус %d км "
                        "на %d локаций",
                        city_info.city_name,
                        len(listings),
                        radius,
                        len(extra_cities),
                    )
                    search_cities.extend(extra_cities)
                    blocked = await _fetch_html_listings(
                        client,
                        extra_cities,
                        categories=categories,
                        max_pages=max_pages,
                        budget_max=budget_max,
                        seen_ids=seen_ids,
                        listings=listings,
                    )
                    html_blocked = html_blocked or blocked

            if not listings or html_blocked:
                sitemap_cities = list(search_cities)
                if radius > 0 and len(sitemap_cities) == 1:
                    extra = await _resolve_radius_cities(client, city_info, radius)
                    for item in extra:
                        if item.city_id not in {c.city_id for c in sitemap_cities}:
                            sitemap_cities.append(item)

                fallback = await _fetch_via_sitemap(
                    client,
                    sitemap_cities,
                    city_query=city,
                    budget_max=budget_max,
                    rooms_min=rooms_min,
                    radius=radius,
                )
                added = 0
                for item in fallback:
                    if item.id in seen_ids:
                        continue
                    seen_ids.add(item.id)
                    listings.append(item)
                    added += 1
                logger.info(
                    "WG-Gesucht: sitemap+API для %s — +%d (всего %d)",
                    city_info.city_name,
                    added,
                    len(listings),
                )

        logger.info(
            "WG-Gesucht: получено %d объявлений для %s",
            len(listings),
            city_info.city_name if city_info else city,
        )
        return listings

    async def load_details(self, listings: list[ListingData]) -> None:
        if not listings:
            return

        pending = [
            listing
            for listing in listings
            if not listing.raw_data.get("api_loaded")
        ]
        if not pending:
            return

        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
        async with httpx.AsyncClient(
            headers=_API_HEADERS,
            timeout=DETAIL_TIMEOUT,
            follow_redirects=True,
        ) as client:
            await asyncio.gather(
                *(_load_one(client, listing, semaphore) for listing in pending)
            )


async def _fetch_html_listings(
    client: httpx.AsyncClient,
    cities: list[_CityInfo],
    *,
    categories: list[tuple[str, str]],
    max_pages: int,
    budget_max: Any,
    seen_ids: set[str],
    listings: list[ListingData],
) -> bool:
    """HTML-выдача по списку городов. True, если хотя бы раз был CAPTCHA."""
    html_blocked = False

    for city_info in cities:
        for category_code, category_slug in categories:
            for page in range(1, max_pages + 1):
                url = build_search_url(
                    city_info,
                    category_code=category_code,
                    category_slug=category_slug,
                    page=page,
                    budget_max=budget_max,
                )
                try:
                    await polite_delay()
                    response = await client.get(url)
                    response.raise_for_status()
                except httpx.HTTPStatusError as error:
                    status = error.response.status_code
                    if status in (403, 429):
                        await alert_http_status("wggesucht", status, url=url)
                    if page == 1:
                        logger.warning(
                            "WG-Gesucht: %s/%s недоступен — %s",
                            category_slug,
                            city_info.city_name,
                            error,
                        )
                    break
                except httpx.HTTPError as error:
                    if page == 1:
                        logger.warning(
                            "WG-Gesucht: %s/%s недоступен — %s",
                            category_slug,
                            city_info.city_name,
                            error,
                        )
                    break

                if _is_blocked_html(response.text):
                    html_blocked = True
                    await alert_blocked_html(
                        "wggesucht",
                        response.text,
                        context=f"{category_slug}/{city_info.city_name}",
                    )
                    logger.warning(
                        "WG-Gesucht: выдача %s/%s заблокирована (CAPTCHA) — "
                        "переключаюсь на sitemap+API",
                        category_slug,
                        city_info.city_name,
                    )
                    break

                batch = _parse_search_html(response.text)
                if not batch:
                    break

                added = 0
                for item in batch:
                    if item.id in seen_ids:
                        continue
                    seen_ids.add(item.id)
                    item.raw_data["search_city"] = city_info.city_name
                    listings.append(item)
                    added += 1

                logger.info(
                    "WG-Gesucht: %s/%s стр. %d, +%d (всего %d)",
                    category_slug,
                    city_info.city_name,
                    page,
                    added,
                    len(listings),
                )
                if added == 0:
                    break
                if page < max_pages:
                    await asyncio.sleep(0.8)

            if html_blocked:
                break
        if html_blocked:
            break

    return html_blocked


def city_to_slug(city: str) -> str:
    """Freiburg im Breisgau → Freiburg-im-Breisgau."""
    slug = city.strip()
    for source, replacement in _UMLAUTS.items():
        slug = slug.replace(source, replacement)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug)
    return slug.strip("-")


def build_search_url(
    city: _CityInfo | str,
    *,
    category_code: str,
    category_slug: str,
    page: int = 1,
    budget_max: int | float | None = None,
) -> str:
    """URL выдачи: /wohnungen-in-{Stadt}.{city_id}.{cat}.{page}.0.html."""
    if isinstance(city, _CityInfo):
        city_id = city.city_id
        slug = city.slug
        city_name = city.city_name
    else:
        city_id = "0"
        slug = city_to_slug(str(city))
        city_name = str(city)

    path = f"/{category_slug}-in-{slug}.{city_id}.{category_code}.{page}.0.html"
    params: dict[str, str | int] = {
        "offer_filter": "1",
        "city_id": city_id,
        "categories[]": category_code,
        "rent_types[]": "0",
        "noDeact": "1",
        "autocompinp": city_name,
        "country_code": "de",
        "city_name": city_name,
    }
    if budget_max is not None and int(budget_max) > 0:
        params["rMax"] = int(budget_max)
    return f"{BASE_URL}{path}?{urlencode(params, doseq=True)}"


def _categories_for_search(rooms_min: Any) -> list[tuple[str, str]]:
    """1-Zimmer-Wohnungen и Wohnungen; 1-Zimmer пропускаем при rooms_min > 1.5."""
    categories: list[tuple[str, str]] = []
    include_studio = True
    if rooms_min is not None:
        try:
            include_studio = float(rooms_min) <= 1.5
        except (TypeError, ValueError):
            include_studio = True
    if include_studio:
        categories.append(("1", _CATEGORY_SLUGS["1"]))
    categories.append(("2", _CATEGORY_SLUGS["2"]))
    return categories


async def _resolve_city(client: httpx.AsyncClient, city: str) -> _CityInfo | None:
    """Ищет city_id через публичный API /api/location/cities/names/{query}."""
    query = city.strip()
    if not query:
        return None

    for candidate in _city_query_variants(query):
        try:
            await polite_delay(min_sec=0.8, max_sec=1.5)
            response = await client.get(
                f"{BASE_URL}/api/location/cities/names/{candidate}",
                headers=_API_HEADERS,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            logger.debug("WG-Gesucht city lookup %r: %s", candidate, error)
            continue

        payload = response.json()
        cities = payload.get("_embedded", {}).get("cities", [])
        if not isinstance(cities, list) or not cities:
            continue

        best = cities[0]
        if not isinstance(best, dict):
            continue
        city_id = str(best.get("city_id") or "").strip()
        city_name = str(best.get("city_name") or candidate).strip()
        if not city_id:
            continue
        return _CityInfo(
            city_id=city_id,
            city_name=city_name,
            slug=city_to_slug(city_name),
            federated_state_id=str(best.get("federated_state_id") or "").strip(),
        )
    return None


async def _geocode_city(
    client: httpx.AsyncClient,
    city_name: str,
    *,
    country: str = "Germany",
) -> tuple[float, float] | None:
    """Координаты города через Nominatim (кэш в памяти)."""
    key = city_name.strip().casefold()
    if key in _geocode_cache:
        return _geocode_cache[key]

    try:
        await polite_delay(min_sec=1.0, max_sec=1.5)
        response = await client.get(
            _NOMINATIM_URL,
            params={
                "city": city_name,
                "country": country,
                "format": "json",
                "limit": 1,
            },
            headers={"User-Agent": _GEOCODE_UA},
            timeout=15.0,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as error:
        logger.debug("Nominatim %r: %s", city_name, error)
        _geocode_cache[key] = None
        return None

    if not isinstance(payload, list) or not payload:
        _geocode_cache[key] = None
        return None

    first = payload[0]
    if not isinstance(first, dict):
        _geocode_cache[key] = None
        return None

    try:
        coords = (float(first["lat"]), float(first["lon"]))
    except (KeyError, TypeError, ValueError):
        _geocode_cache[key] = None
        return None

    _geocode_cache[key] = coords
    return coords


async def _nearby_place_names(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
    radius_km: int,
) -> list[str]:
    """Населённые пункты в радиусе через Overpass API."""
    cache_key = (f"{lat:.4f},{lon:.4f}", radius_km)
    if cache_key in _nearby_names_cache:
        return _nearby_names_cache[cache_key]

    radius_m = max(radius_km, 1) * 1000
    query = (
        f"[out:json][timeout:25];("
        f'node["place"~"^(city|town|village)$"]["name"]'
        f"(around:{radius_m},{lat},{lon});"
        f");out body;"
    )
    names: list[str] = []
    try:
        await polite_delay(min_sec=1.0, max_sec=1.5)
        response = await client.post(
            _OVERPASS_URL,
            data={"data": query},
            headers={"User-Agent": _GEOCODE_UA},
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
        elements = payload.get("elements") if isinstance(payload, dict) else None
        if isinstance(elements, list):
            for element in elements:
                if not isinstance(element, dict):
                    continue
                tags = element.get("tags")
                if not isinstance(tags, dict):
                    continue
                name = str(tags.get("name") or "").strip()
                if name and name not in names:
                    names.append(name)
    except httpx.HTTPError as error:
        logger.warning("Overpass nearby %d км: %s", radius_km, error)

    _nearby_names_cache[cache_key] = names
    return names


async def _resolve_radius_cities(
    client: httpx.AsyncClient,
    primary: _CityInfo,
    radius_km: int,
) -> list[_CityInfo]:
    """Соседние города WG-Gesucht в пределах радиуса Umkreis."""
    if radius_km <= 0:
        return []

    coords = await _geocode_city(client, primary.city_name)
    if coords is None:
        return []

    lat, lon = coords
    names = await _nearby_place_names(client, lat, lon, radius_km)
    resolved: list[_CityInfo] = []
    seen_ids: set[str] = {primary.city_id}

    for name in names[: _MAX_RADIUS_CITIES * 2]:
        city = await _resolve_city(client, name)
        if city is None or city.city_id in seen_ids:
            continue
        if (
            primary.federated_state_id
            and city.federated_state_id
            and city.federated_state_id != primary.federated_state_id
        ):
            continue
        seen_ids.add(city.city_id)
        resolved.append(city)
        if len(resolved) >= _MAX_RADIUS_CITIES:
            break

    return resolved


def _city_query_variants(city: str) -> list[str]:
    """Несколько вариантов запроса: полное имя и первый токен."""
    cleaned = " ".join(city.split())
    variants = [cleaned]
    token = cleaned.split()[0]
    if token and token not in variants:
        variants.append(token)
    return variants


def _is_blocked_html(html: str) -> bool:
    """Cloudflare/CAPTCHA вместо списка объявлений."""
    if detect_block_reason(html):
        return True
    if not html:
        return True
    if ".offer_list_item" in html or "data-id=" in html:
        return False
    return False


def _parse_search_html(html: str) -> list[ListingData]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".offer_list_item, .wgg_card.offer_list_item, .wgg_card")
    if not cards:
        cards = soup.select(".offer_list_item")

    listings: list[ListingData] = []
    seen: set[str] = set()
    for card in cards:
        parsed = _parse_card(card)
        if parsed is None or parsed.id in seen:
            continue
        seen.add(parsed.id)
        listings.append(parsed)
    return listings


def _parse_card(card: Tag) -> ListingData | None:
    offer_id = str(card.get("data-id") or "").strip()
    if not offer_id:
        link = card.select_one('a[href*=".html"]')
        if link is not None:
            match = re.search(r"\.(\d{5,})\.html", str(link.get("href") or ""))
            if match:
                offer_id = match.group(1)
    if not offer_id:
        return None

    title_node = card.select_one("h2.truncate_title a, h2 a, .truncate_title a")
    title = _text(title_node)
    if not title:
        image = card.select_one("img[alt]")
        if image is not None:
            alt = str(image.get("alt") or "")
            if alt.lower().startswith("anzeigenbild:"):
                title = alt.split(":", 1)[-1].strip()

    link_node = card.select_one('a[href*=".html"]')
    href = str(link_node.get("href") or "") if link_node is not None else ""
    url = href if href.startswith("http") else f"{BASE_URL}{href}" if href else ""

    location_node = card.select_one(".col-xs-11 span, .col-xs-11")
    location_text = _text(location_node)
    location, rooms = _split_location(location_text)

    price = None
    sqm = None
    middle = card.select_one(".middle")
    if middle is not None:
        price_node = middle.select_one(".col-xs-3 b, .col-xs-3")
        price = parse_first_amount(_text(price_node) or "")
        sqm_nodes = middle.select(".col-xs-3 b, .col-xs-3")
        if sqm_nodes:
            sqm_text = _text(sqm_nodes[-1])
            sqm = parse_sqm(sqm_text or "") if sqm_text else None
        if sqm is None:
            sqm = parse_sqm(middle.get_text(" ", strip=True))

    if rooms is None and title:
        rooms = _rooms_from_text(title)
    if rooms is None and location_text:
        rooms = _rooms_from_text(location_text)

    image = card.select_one("img.img-responsive, img")
    image_url = None
    if image is not None:
        src = image.get("src") or image.get("data-src")
        if isinstance(src, str) and src.startswith("http"):
            image_url = src

    published_at = parse_german_listing_date(card.get_text(" ", strip=True))

    if not title:
        title = location or f"Angebot {offer_id}"

    return ListingData(
        id=offer_id,
        title=title[:200],
        price=price,
        size_sqm=sqm,
        rooms=rooms,
        location=location,
        url=url,
        image_url=image_url,
        source_platform="wggesucht",
        description="",
        published_at=published_at,
        raw_data={
            "location_text": location_text,
            "card_html_id": card.get("id"),
        },
    )


def _split_location(text: str | None) -> tuple[str | None, float | None]:
    if not text:
        return None, None
    parts = [part.strip() for part in text.split("|") if part.strip()]
    rooms = _rooms_from_text(parts[0]) if parts else None
    location = " | ".join(parts[1:]) if len(parts) > 1 else text.strip()
    return location or text.strip(), rooms


def _rooms_from_text(text: str | None) -> float | None:
    if not text:
        return None
    if re.search(r"Einzimmer", text, re.IGNORECASE):
        return 1.0
    match = _ROOMS_IN_TEXT.search(text)
    return parse_number(match.group(1)) if match else None


def _resolve_rooms(data: dict[str, Any], listing: ListingData) -> float | None:
    """WG-Gesucht часто отдаёт number_of_rooms=0 для 1-Zimmer — не затираем карточку."""
    api_rooms = _float_or_none(data.get("number_of_rooms"))
    if api_rooms is not None and api_rooms > 0:
        return api_rooms

    if listing.rooms is not None and listing.rooms > 0:
        return listing.rooms

    title = str(data.get("offer_title") or listing.title or "")
    inferred = _rooms_from_text(title)
    if inferred is not None:
        return inferred

    category = str(data.get("category") or listing.raw_data.get("category") or "")
    if category == "1":
        return 1.0
    return None


def _text(node: Tag | None) -> str | None:
    if node is None:
        return None
    text = " ".join(node.get_text(" ", strip=True).split())
    return text or None


async def _get_sitemap_xml(client: httpx.AsyncClient) -> str:
    """Кэширует sitemap на час: ~750 KB gzip, без CAPTCHA."""
    global _sitemap_cache
    now = time.monotonic()
    if _sitemap_cache is not None:
        cached_at, content = _sitemap_cache
        if now - cached_at < SITEMAP_CACHE_TTL:
            return content

    async with _sitemap_lock:
        now = time.monotonic()
        if _sitemap_cache is not None:
            cached_at, content = _sitemap_cache
            if now - cached_at < SITEMAP_CACHE_TTL:
                return content

        await polite_delay()
        response = await client.get(SITEMAP_URL, headers=_API_HEADERS, timeout=SITEMAP_TIMEOUT)
        if response.status_code in (403, 429):
            await alert_http_status("wggesucht", response.status_code, url=SITEMAP_URL)
        response.raise_for_status()
        content = gzip.decompress(response.content).decode("utf-8", errors="replace")
        _sitemap_cache = (time.monotonic(), content)
        logger.info("WG-Gesucht: sitemap загружен (%d URL)", content.count("<loc>"))
        return content


def _normalize_token(value: str) -> str:
    token = city_to_slug(value).casefold()
    return token.replace("-", "")


def _city_match_tokens(city_info: _CityInfo, city_query: str) -> list[str]:
    """Токены для поиска города в URL sitemap."""
    tokens: list[str] = []
    for raw in (city_info.slug, city_info.city_name, city_query):
        cleaned = " ".join(str(raw).split())
        if not cleaned:
            continue
        tokens.append(_normalize_token(cleaned))
        first = cleaned.split()[0]
        if first:
            tokens.append(_normalize_token(first))
        if "(" in cleaned:
            inside = cleaned.split("(", 1)[1].split(")", 1)[0].strip()
            if inside:
                tokens.append(_normalize_token(inside))
    seen: set[str] = set()
    unique: list[str] = []
    for token in tokens:
        if len(token) < 3 or token in seen:
            continue
        seen.add(token)
        unique.append(token)
    return unique


def _sitemap_category_slugs(rooms_min: Any) -> set[str]:
    slugs = {"wohnungen"}
    include_studio = True
    if rooms_min is not None:
        try:
            include_studio = float(rooms_min) <= 1.5
        except (TypeError, ValueError):
            include_studio = True
    if include_studio:
        slugs.add("1-zimmer-wohnungen")
    return slugs


def _url_matches_city(location: str, tokens: list[str]) -> bool:
    folded = _normalize_token(location)
    return any(token in folded for token in tokens)


def _collect_sitemap_candidates(
    xml: str,
    *,
    city_tokens: list[str],
    category_slugs: set[str],
) -> list[tuple[str, str, str]]:
    """Возвращает [(offer_id, category_slug, url), ...] без дубликатов."""
    results: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for match in _SITEMAP_LINE.finditer(xml):
        category = match.group("category")
        if category not in category_slugs:
            continue
        location = match.group("location")
        if not _url_matches_city(location, city_tokens):
            continue
        offer_id = match.group("offer_id")
        if offer_id in seen:
            continue
        seen.add(offer_id)
        url = match.group(0)
        results.append((offer_id, category, url))
        if len(results) >= MAX_SITEMAP_SCAN:
            break
    return results


def _category_allowed(category: object, rooms_min: Any) -> bool:
    cat = str(category or "")
    if cat == "0":
        return False
    if cat == "3":
        return False
    if cat == "1":
        if rooms_min is None:
            return True
        try:
            return float(rooms_min) <= 1.5
        except (TypeError, ValueError):
            return True
    return True


def _api_price(data: dict[str, Any]) -> tuple[int | None, str]:
    warm = _float_or_none(data.get("total_costs"))
    rent = _float_or_none(data.get("rent_costs"))
    utility = _float_or_none(data.get("utility_costs"))
    if warm is not None:
        return int(round(warm)), "warm"
    if rent is not None:
        kind = infer_price_kind(
            kalt=int(round(rent)),
            neben=int(round(utility)) if utility is not None else None,
            default="kalt",
        )
        return int(round(rent)), kind
    return None, "unknown"


def _passes_api_filters(
    data: dict[str, Any],
    *,
    city_info: _CityInfo,
    allowed_city_ids: set[str] | None,
    budget_max: Any,
    rooms_min: Any,
    radius: int,
) -> bool:
    if not _category_allowed(data.get("category"), rooms_min):
        return False

    offer_city_id = str(data.get("city_id") or "")
    if radius <= 0:
        if offer_city_id and offer_city_id != city_info.city_id:
            return False
    elif allowed_city_ids and offer_city_id and offer_city_id not in allowed_city_ids:
        return False

    price, _kind = _api_price(data)
    if budget_max is not None and price is not None:
        try:
            if price > int(budget_max):
                return False
        except (TypeError, ValueError):
            pass

    if rooms_min is not None:
        try:
            min_rooms = float(rooms_min)
        except (TypeError, ValueError):
            min_rooms = None
        if min_rooms is not None:
            api_rooms = _float_or_none(data.get("number_of_rooms"))
            if api_rooms is not None and api_rooms > 0:
                rooms = api_rooms
            else:
                rooms = _rooms_from_text(str(data.get("offer_title") or ""))
            if rooms is not None and rooms < min_rooms:
                return False

    return True


def _build_offer_url(data: dict[str, Any], *, fallback_url: str = "") -> str:
    if fallback_url:
        return fallback_url
    offer_id = str(data.get("offer_id") or "").strip()
    category = str(data.get("category") or "2")
    slug = _CATEGORY_SLUGS.get(category, "wohnungen")
    district = str(data.get("district_custom") or "").strip()
    location_slug = city_to_slug(district) if district else "Angebot"
    return f"{BASE_URL}/{slug}-in-{location_slug}.{offer_id}.html"


def _listing_from_api_data(
    data: dict[str, Any],
    *,
    fallback_url: str = "",
) -> ListingData:
    offer_id = str(data.get("offer_id") or "").strip()
    title = str(data.get("offer_title") or f"Angebot {offer_id}").strip()
    price, price_kind = _api_price(data)
    sqm = _float_or_none(data.get("property_size"))
    rooms = _resolve_rooms(data, ListingData(
        id=offer_id,
        title=title,
        price=price,
        size_sqm=sqm,
        rooms=None,
        location=None,
        url=fallback_url,
        image_url=None,
        source_platform="wggesucht",
        raw_data={"category": data.get("category")},
    ))

    street = str(data.get("street") or "").strip()
    district = str(data.get("district_custom") or "").strip()
    postcode = str(data.get("postcode") or "").strip()
    location_parts = [part for part in (street, postcode, district) if part]
    location = ", ".join(location_parts) if location_parts else district or None

    description_parts = [
        str(data.get(key) or "").strip()
        for key in (
            "freetext_property_description",
            "freetext_area_description",
            "freetext_flatshare",
            "freetext_other",
        )
    ]
    description = "\n\n".join(part for part in description_parts if part)

    edited = str(data.get("date_edited") or data.get("date_created") or "")
    published = parse_german_listing_date(edited)
    warm_val = _float_or_none(data.get("total_costs"))
    kalt_val = _float_or_none(data.get("rent_costs"))
    nk_val = _float_or_none(data.get("utility_costs"))

    return ListingData(
        id=offer_id,
        title=title[:200],
        price=price,
        size_sqm=sqm,
        rooms=rooms,
        location=location,
        url=_build_offer_url(data, fallback_url=fallback_url),
        image_url=None,
        source_platform="wggesucht",
        description=description,
        published_at=published,
        raw_data={
            "price_kind": price_kind,
            "api_loaded": True,
            "source": "sitemap",
            "rent_breakdown": {
                "warm": int(round(warm_val)) if warm_val is not None else None,
                "kalt": int(round(kalt_val)) if kalt_val is not None else None,
                "neben": int(round(nk_val)) if nk_val is not None else None,
            },
            "api": {
                "rent_costs": data.get("rent_costs"),
                "total_costs": data.get("total_costs"),
                "utility_costs": data.get("utility_costs"),
                "category": data.get("category"),
                "number_of_rooms": data.get("number_of_rooms"),
                "city_id": data.get("city_id"),
                "rent_type": data.get("rent_type"),
                "available_from_date": data.get("available_from_date"),
                "available_to_date": data.get("available_to_date"),
            },
        },
    )


async def _fetch_offer_api(
    client: httpx.AsyncClient,
    offer_id: str,
    *,
    fallback_url: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any] | None:
    async with semaphore:
        try:
            await polite_delay(min_sec=0.8, max_sec=1.5)
            response = await client.get(
                f"{BASE_URL}/api/offers/{offer_id}",
                headers=_API_HEADERS,
            )
            if response.status_code in (403, 429):
                await alert_http_status(
                    "wggesucht",
                    response.status_code,
                    url=f"{BASE_URL}/api/offers/{offer_id}",
                )
            response.raise_for_status()
        except httpx.HTTPError as error:
            logger.debug("WG-Gesucht API %s: %s", offer_id, error)
            return None
    data = response.json()
    return data if isinstance(data, dict) else None


async def _fetch_via_sitemap(
    client: httpx.AsyncClient,
    cities: list[_CityInfo],
    *,
    city_query: str,
    budget_max: Any,
    rooms_min: Any,
    radius: int,
) -> list[ListingData]:
    """Обход CAPTCHA: ID из sitemap, детали через /api/offers/{id}."""
    if not cities:
        return []

    primary = cities[0]
    try:
        xml = await _get_sitemap_xml(client)
    except httpx.HTTPError as error:
        logger.warning("WG-Gesucht: sitemap недоступен — %s", error)
        return []

    tokens: list[str] = []
    seen_tokens: set[str] = set()
    for city in cities:
        for token in _city_match_tokens(city, city_query):
            if token not in seen_tokens:
                seen_tokens.add(token)
                tokens.append(token)

    allowed_city_ids = {city.city_id for city in cities}
    categories = _sitemap_category_slugs(rooms_min)
    candidates = _collect_sitemap_candidates(
        xml,
        city_tokens=tokens,
        category_slugs=categories,
    )
    logger.info(
        "WG-Gesucht: sitemap нашёл %d кандидатов для %s (локаций %d, токены %s)",
        len(candidates),
        primary.city_name,
        len(cities),
        tokens[:6],
    )
    if not candidates:
        return []

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    listings: list[ListingData] = []
    for offer_id, _category, url in candidates[:MAX_SITEMAP_API_CALLS]:
        data = await _fetch_offer_api(
            client,
            offer_id,
            fallback_url=url,
            semaphore=semaphore,
        )
        if data is None:
            continue
        if not _passes_api_filters(
            data,
            city_info=primary,
            allowed_city_ids=allowed_city_ids,
            budget_max=budget_max,
            rooms_min=rooms_min,
            radius=radius,
        ):
            continue
        listings.append(_listing_from_api_data(data, fallback_url=url))

    return listings


def _apply_api_data(listing: ListingData, data: dict[str, Any]) -> None:
    title = str(data.get("offer_title") or "").strip()
    if title:
        listing.title = title[:200]

    warm = _float_or_none(data.get("total_costs"))
    utility = _float_or_none(data.get("utility_costs"))
    rent = _float_or_none(data.get("rent_costs"))
    if warm is not None:
        listing.price = int(round(warm))
        listing.raw_data["price_kind"] = "warm"
    elif rent is not None:
        listing.price = int(round(rent))
        listing.raw_data["price_kind"] = infer_price_kind(
            kalt=int(round(rent)),
            neben=int(round(utility)) if utility is not None else None,
            default="kalt",
        )

    sqm = _float_or_none(data.get("property_size"))
    if sqm is not None:
        listing.size_sqm = sqm

    rooms = _resolve_rooms(data, listing)
    if rooms is not None:
        listing.rooms = rooms

    street = str(data.get("street") or "").strip()
    district = str(data.get("district_custom") or "").strip()
    postcode = str(data.get("postcode") or "").strip()
    location_parts = [part for part in (street, postcode, district) if part]
    if location_parts:
        listing.location = ", ".join(location_parts)

    description_parts = [
        str(data.get(key) or "").strip()
        for key in (
            "freetext_property_description",
            "freetext_area_description",
            "freetext_flatshare",
            "freetext_other",
        )
    ]
    description = "\n\n".join(part for part in description_parts if part)
    if description:
        listing.description = description

    edited = str(data.get("date_edited") or data.get("date_created") or "")
    published = parse_german_listing_date(edited)
    if published is not None:
        listing.published_at = published

    if not listing.url:
        listing.url = _build_offer_url(data)

    listing.raw_data["api_loaded"] = True
    listing.raw_data["rent_breakdown"] = {
        "warm": int(round(warm)) if warm is not None else None,
        "kalt": int(round(rent)) if rent is not None else None,
        "neben": int(round(utility)) if utility is not None else None,
    }
    listing.raw_data["api"] = {
        "rent_costs": data.get("rent_costs"),
        "total_costs": data.get("total_costs"),
        "utility_costs": data.get("utility_costs"),
        "category": data.get("category"),
        "number_of_rooms": data.get("number_of_rooms"),
        "city_id": data.get("city_id"),
        "rent_type": data.get("rent_type"),
        "available_from_date": data.get("available_from_date"),
        "available_to_date": data.get("available_to_date"),
    }


async def _load_one(
    client: httpx.AsyncClient,
    listing: ListingData,
    semaphore: asyncio.Semaphore,
) -> None:
    async with semaphore:
        try:
            await polite_delay(min_sec=0.8, max_sec=1.5)
            response = await client.get(f"{BASE_URL}/api/offers/{listing.id}")
            if response.status_code in (403, 429):
                await alert_http_status(
                    "wggesucht",
                    response.status_code,
                    url=f"{BASE_URL}/api/offers/{listing.id}",
                )
            response.raise_for_status()
        except httpx.HTTPError as error:
            logger.warning(
                "WG-Gesucht: детали %s недоступны (%s)", listing.id, error
            )
            return

    data = response.json()
    if not isinstance(data, dict):
        return

    _apply_api_data(listing, data)


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
