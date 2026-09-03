"""Проверка города в Германии и определение Bundesland."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Final

import httpx

from services.http_politeness import polite_delay_for, polite_delay_geo
from services.translator import normalize_and_translate_user_input

logger = logging.getLogger(__name__)

_WG_BASE: Final[str] = "https://www.wg-gesucht.de"
_NOMINATIM_URL: Final[str] = "https://nominatim.openstreetmap.org/search"
_GEOCODE_UA: Final[str] = "ImmomatchBot/1.0 (city bundesland lookup)"
_PLZ_RE: Final[re.Pattern[str]] = re.compile(r"\b(\d{5})\b")

_HEADERS: Final[dict[str, str]] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "de-DE,de;q=0.9",
    "X-Client-Id": "wg_mobile_app",
}

# ID земель в API WG-Gesucht → официальное немецкое название.
_WG_STATE_NAMES: Final[dict[str, str]] = {
    "2": "Nordrhein-Westfalen",
    "3": "Bayern",
    "4": "Baden-Württemberg",
    "5": "Berlin",
    "6": "Brandenburg",
    "7": "Niedersachsen",
    "8": "Bremen",
    "9": "Sachsen",
    "10": "Hessen",
    "11": "Sachsen-Anhalt",
    "12": "Thüringen",
    "13": "Schleswig-Holstein",
    "14": "Mecklenburg-Vorpommern",
    "15": "Hamburg",
    "16": "Rheinland-Pfalz",
    "17": "Saarland",
}

# Первые 2 цифры PLZ → земля (однозначные префиксы; смешанные пропускаем).
_PLZ_PREFIX_LAND: Final[dict[str, str]] = {
    "01": "Sachsen",
    "02": "Sachsen",
    "04": "Sachsen",
    "06": "Sachsen-Anhalt",
    "07": "Thüringen",
    "08": "Sachsen",
    "09": "Sachsen",
    "10": "Berlin",
    "12": "Berlin",
    "13": "Berlin",
    "14": "Brandenburg",
    "15": "Brandenburg",
    "16": "Brandenburg",
    "17": "Mecklenburg-Vorpommern",
    "18": "Mecklenburg-Vorpommern",
    "19": "Mecklenburg-Vorpommern",
    "20": "Hamburg",
    "21": "Niedersachsen",
    "22": "Hamburg",
    "23": "Schleswig-Holstein",
    "24": "Schleswig-Holstein",
    "25": "Schleswig-Holstein",
    "26": "Niedersachsen",
    "27": "Niedersachsen",
    "28": "Bremen",
    "29": "Niedersachsen",
    "30": "Niedersachsen",
    "31": "Niedersachsen",
    "32": "Nordrhein-Westfalen",
    "33": "Nordrhein-Westfalen",
    "34": "Hessen",
    "35": "Hessen",
    "36": "Hessen",
    "37": "Niedersachsen",
    "38": "Niedersachsen",
    "39": "Sachsen-Anhalt",
    "40": "Nordrhein-Westfalen",
    "41": "Nordrhein-Westfalen",
    "42": "Nordrhein-Westfalen",
    "44": "Nordrhein-Westfalen",
    "45": "Nordrhein-Westfalen",
    "46": "Nordrhein-Westfalen",
    "47": "Nordrhein-Westfalen",
    "48": "Nordrhein-Westfalen",
    "49": "Niedersachsen",
    "50": "Nordrhein-Westfalen",
    "51": "Nordrhein-Westfalen",
    "52": "Nordrhein-Westfalen",
    "53": "Nordrhein-Westfalen",
    "54": "Rheinland-Pfalz",
    "55": "Rheinland-Pfalz",
    "56": "Rheinland-Pfalz",
    "57": "Nordrhein-Westfalen",
    "58": "Nordrhein-Westfalen",
    "59": "Nordrhein-Westfalen",
    "60": "Hessen",
    "61": "Hessen",
    "63": "Hessen",
    "64": "Hessen",
    "65": "Hessen",
    "66": "Saarland",
    "67": "Rheinland-Pfalz",
    "68": "Baden-Württemberg",
    "69": "Baden-Württemberg",
    "70": "Baden-Württemberg",
    "71": "Baden-Württemberg",
    "72": "Baden-Württemberg",
    "73": "Baden-Württemberg",
    "74": "Baden-Württemberg",
    "75": "Baden-Württemberg",
    "76": "Baden-Württemberg",
    "77": "Baden-Württemberg",
    "78": "Baden-Württemberg",
    "79": "Baden-Württemberg",
    "80": "Bayern",
    "81": "Bayern",
    "82": "Bayern",
    "83": "Bayern",
    "84": "Bayern",
    "85": "Bayern",
    "86": "Bayern",
    "87": "Bayern",
    "88": "Baden-Württemberg",
    "89": "Baden-Württemberg",
    "90": "Bayern",
    "91": "Bayern",
    "92": "Bayern",
    "93": "Bayern",
    "94": "Bayern",
    "95": "Bayern",
    "96": "Bayern",
    "97": "Bayern",
    "98": "Thüringen",
    "99": "Thüringen",
}

_LAND_ALIASES: Final[dict[str, str]] = {
    "badenwurttemberg": "Baden-Württemberg",
    "badenwuerttemberg": "Baden-Württemberg",
    "baden-wurttemberg": "Baden-Württemberg",
    "baden-wuerttemberg": "Baden-Württemberg",
    "bayern": "Bayern",
    "bavaria": "Bayern",
    "berlin": "Berlin",
    "brandenburg": "Brandenburg",
    "bremen": "Bremen",
    "hamburg": "Hamburg",
    "hessen": "Hessen",
    "mecklenburgvorpommern": "Mecklenburg-Vorpommern",
    "mecklenburg-vorpommern": "Mecklenburg-Vorpommern",
    "niedersachsen": "Niedersachsen",
    "lower saxony": "Niedersachsen",
    "nordrheinwestfalen": "Nordrhein-Westfalen",
    "nordrhein-westfalen": "Nordrhein-Westfalen",
    "north rhine-westphalia": "Nordrhein-Westfalen",
    "rheinlandpfalz": "Rheinland-Pfalz",
    "rheinland-pfalz": "Rheinland-Pfalz",
    "saarland": "Saarland",
    "sachsen": "Sachsen",
    "saxony": "Sachsen",
    "sachsenanhalt": "Sachsen-Anhalt",
    "sachsen-anhalt": "Sachsen-Anhalt",
    "schleswigholstein": "Schleswig-Holstein",
    "schleswig-holstein": "Schleswig-Holstein",
    "thuringen": "Thüringen",
    "thueringen": "Thüringen",
    "thüringen": "Thüringen",
}


@dataclass(slots=True, frozen=True)
class CityGeo:
    """Канонический город Германии и его земля."""

    city_de: str
    bundesland: str
    federated_state_id: str = ""


def normalize_bundesland(name: str | None) -> str | None:
    """Приводит название земли к каноническому виду."""
    if not name or not str(name).strip():
        return None
    raw = str(name).strip()
    key = (
        raw.casefold()
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
        .replace(" ", "")
    )
    # Ключи без дефиса.
    key_nodash = key.replace("-", "")
    for alias, canonical in _LAND_ALIASES.items():
        alias_key = alias.replace("-", "").replace(" ", "")
        if key_nodash == alias_key or key == alias:
            return canonical
    # Уже каноническое немецкое имя из таблицы.
    for canonical in _WG_STATE_NAMES.values():
        if canonical.casefold() == raw.casefold():
            return canonical
    return raw


def bundesland_from_plz(plz: str | None) -> str | None:
    """Земля по индексу, если префикс однозначный."""
    if not plz:
        return None
    digits = re.sub(r"\D", "", str(plz))
    if len(digits) < 2:
        return None
    return _PLZ_PREFIX_LAND.get(digits[:2])


def bundesland_from_address(text: str | None) -> str | None:
    """Достаёт PLZ из адреса/заголовка и определяет землю."""
    if not text:
        return None
    match = _PLZ_RE.search(text)
    if not match:
        return None
    return bundesland_from_plz(match.group(1))


def bundesland_from_state_id(state_id: str | None) -> str | None:
    """Немецкое название земли по ID WG-Gesucht."""
    if not state_id:
        return None
    return _WG_STATE_NAMES.get(str(state_id).strip())


async def resolve_city_in_germany(
    city: str,
    *,
    first_name: str = "",
    last_name: str = "",
) -> CityGeo | None:
    """Нормализует город и проверяет, что он есть в Германии.

    Сначала WG API (city_id + federated_state_id), затем Nominatim.
    """
    raw = " ".join(str(city or "").split())
    if not raw:
        return None

    try:
        normalized = await normalize_and_translate_user_input(
            first_name or "User",
            last_name or "User",
            raw,
        )
        city_de = str(normalized.get("city_de") or raw).strip()
    except Exception:
        logger.exception("Нормализация города %r не удалась", raw)
        city_de = raw

    async with httpx.AsyncClient(
        headers=_HEADERS,
        timeout=20.0,
        follow_redirects=True,
    ) as client:
        wg = await _resolve_via_wg(client, city_de)
        if wg is not None:
            return wg
        if city_de.casefold() != raw.casefold():
            wg = await _resolve_via_wg(client, raw)
            if wg is not None:
                return wg
        return await _resolve_via_nominatim(client, city_de)


async def _resolve_via_wg(client: httpx.AsyncClient, city: str) -> CityGeo | None:
    query = " ".join(city.split())
    if not query:
        return None

    for candidate in _city_variants(query):
        try:
            await polite_delay_for("wggesucht")
            response = await client.get(
                f"{_WG_BASE}/api/location/cities/names/{candidate}",
                headers=_HEADERS,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            logger.debug("WG city lookup %r: %s", candidate, error)
            continue

        payload = response.json()
        cities = payload.get("_embedded", {}).get("cities", [])
        if not isinstance(cities, list) or not cities:
            continue

        best = _pick_city_match(cities, candidate)
        if not isinstance(best, dict):
            continue
        city_id = str(best.get("city_id") or "").strip()
        city_name = str(best.get("city_name") or candidate).strip()
        state_id = str(best.get("federated_state_id") or "").strip()
        if not city_id or not city_name:
            continue
        land = bundesland_from_state_id(state_id)
        if not land:
            land = await _nominatim_state(client, city_name)
        if not land:
            continue
        return CityGeo(
            city_de=city_name,
            bundesland=land,
            federated_state_id=state_id,
        )
    return None


async def _resolve_via_nominatim(
    client: httpx.AsyncClient, city: str
) -> CityGeo | None:
    data = await _nominatim_place(client, city)
    if data is None:
        return None
    address = data.get("address")
    if not isinstance(address, dict):
        return None
    land = normalize_bundesland(
        str(address.get("state") or address.get("ISO3166-2-lvl4") or "")
    )
    if land and land.startswith("DE-"):
        land = bundesland_from_iso(land)
    city_name = (
        str(address.get("city") or address.get("town") or address.get("village") or "")
        or str(data.get("name") or city)
    ).strip()
    if not city_name or not land:
        return None
    return CityGeo(city_de=city_name, bundesland=land, federated_state_id="")


async def _nominatim_state(client: httpx.AsyncClient, city: str) -> str | None:
    data = await _nominatim_place(client, city)
    if data is None:
        return None
    address = data.get("address")
    if not isinstance(address, dict):
        return None
    land = normalize_bundesland(
        str(address.get("state") or address.get("ISO3166-2-lvl4") or "")
    )
    if land and land.startswith("DE-"):
        return bundesland_from_iso(land)
    return land


async def _nominatim_place(
    client: httpx.AsyncClient, city: str
) -> dict[str, Any] | None:
    try:
        await polite_delay_geo()
        response = await client.get(
            _NOMINATIM_URL,
            params={
                "city": city,
                "country": "Germany",
                "format": "json",
                "addressdetails": 1,
                "limit": 1,
            },
            headers={"User-Agent": _GEOCODE_UA},
            timeout=15.0,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as error:
        logger.debug("Nominatim %r: %s", city, error)
        return None
    if not isinstance(payload, list) or not payload:
        return None
    first = payload[0]
    return first if isinstance(first, dict) else None


def bundesland_from_iso(code: str) -> str | None:
    """DE-BW → Baden-Württemberg."""
    mapping = {
        "DE-BW": "Baden-Württemberg",
        "DE-BY": "Bayern",
        "DE-BE": "Berlin",
        "DE-BB": "Brandenburg",
        "DE-HB": "Bremen",
        "DE-HH": "Hamburg",
        "DE-HE": "Hessen",
        "DE-MV": "Mecklenburg-Vorpommern",
        "DE-NI": "Niedersachsen",
        "DE-NW": "Nordrhein-Westfalen",
        "DE-RP": "Rheinland-Pfalz",
        "DE-SL": "Saarland",
        "DE-SN": "Sachsen",
        "DE-ST": "Sachsen-Anhalt",
        "DE-SH": "Schleswig-Holstein",
        "DE-TH": "Thüringen",
    }
    return mapping.get(code.strip().upper())


def _city_variants(city: str) -> list[str]:
    cleaned = " ".join(city.split())
    variants = [cleaned]
    token = cleaned.split()[0]
    if token and token not in variants:
        variants.append(token)
    return variants


def _pick_city_match(cities: list[Any], query: str) -> dict[str, Any] | None:
    normalized = " ".join(query.split()).casefold()
    exact: dict[str, Any] | None = None
    prefix: dict[str, Any] | None = None
    for item in cities:
        if not isinstance(item, dict):
            continue
        name = str(item.get("city_name") or "").strip()
        folded = name.casefold()
        if folded == normalized:
            exact = item
            break
        if folded.startswith(normalized) or normalized.startswith(folded):
            prefix = prefix or item
    return exact or prefix or (cities[0] if isinstance(cities[0], dict) else None)


def same_bundesland(left: str | None, right: str | None) -> bool:
    """Сравнивает земли без учёта написания."""
    a = normalize_bundesland(left)
    b = normalize_bundesland(right)
    if not a or not b:
        return False
    return a.casefold() == b.casefold()
