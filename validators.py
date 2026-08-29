"""Разбор и проверка пользовательского ввода."""

from __future__ import annotations

import re
from typing import Final

# Границы значений анкеты.
CITY_MIN_LEN: Final[int] = 2
CITY_MAX_LEN: Final[int] = 60
NAME_MIN_LEN: Final[int] = 2
NAME_MAX_LEN: Final[int] = 40
BUDGET_MIN: Final[int] = 100
BUDGET_MAX: Final[int] = 20_000
# Ниже этого Warmmiete почти наверняка цена за сутки / Ferienwohnung, не аренда.
MIN_PLAUSIBLE_RENT: Final[int] = 100
# Если указана только Kaltmiete, отсекаем объявления близко к бюджету Warmmiete:
# Nebenkosten почти наверняка выведут итог за лимит.
KALT_BUDGET_BUFFER_MIN: Final[int] = 120
KALT_BUDGET_BUFFER_RATIO: Final[float] = 0.15
ROOMS_MIN: Final[float] = 1.0
ROOMS_MAX: Final[float] = 10.0
HOUSEHOLD_MIN: Final[int] = 1
HOUSEHOLD_MAX: Final[int] = 12
SQM_MIN: Final[float] = 1.0
SQM_MAX: Final[float] = 1_000.0
# Жёстко режем площадь только если она меньше минимума больше чем на эту долю.
# 1,5 Zimmer в Лар часто 28–32 м² при запросе «от 35».
AREA_UNDERSHOOT: Final[float] = 0.20
INCOME_MIN: Final[int] = 0
INCOME_MAX: Final[int] = 100_000
NOTES_MAX_LEN: Final[int] = 500
# Радиус Umkreis на kleinanzeigen.de. 0 — только выбранный город.
SEARCH_RADII: Final[tuple[int, ...]] = (0, 5, 10, 20, 50)
APPLICANT_GENDERS: Final[tuple[str, ...]] = ("male", "female")
HOUSEHOLD_TYPES: Final[tuple[str, ...]] = (
    "single",
    "partner_female",
    "partner_male",
    "family",
    "wg",
)

# Обозначения валюты, которые пользователи дописывают к сумме.
_CURRENCY = re.compile(r"€|eur\b|euro\b|євро\b|евро\b", re.IGNORECASE)
# Пробелы (включая неразрывный) и апострофы как разделители разрядов.
_SPACERS = re.compile(r"[\s\u00a0'’]")
# Точка или запятая ровно перед группой из трёх цифр — разделитель тысяч.
_THOUSANDS = re.compile(r"[.,](?=\d{3}(?:\D|$))")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_CITY_ALLOWED = re.compile(r"^[^\W\d_][\w\s'’\-.()]*$", re.UNICODE)
_NAME_ALLOWED = re.compile(r"^[^\W\d_](?:[\s'’.\-]*[^\W\d_]+)*$", re.UNICODE)
# Единицы площади, которые пользователи дописывают к числу.
_AREA_UNIT = re.compile(r"m[²2]|qm|кв\.?\s*м|м²", re.IGNORECASE)


def parse_number(raw: str) -> float | None:
    """Разбирает число из свободного ввода вида «1.200 €», «1200,50», «2,5».

    Возвращает None, если строку нельзя однозначно понять как число.
    """
    text = _SPACERS.sub("", _CURRENCY.sub("", raw))
    # Немецкая запись целых евро: «690,-»
    text = re.sub(r"[,.]\-$", "", text)
    text = _THOUSANDS.sub("", text)
    text = text.replace(",", ".")

    if not _NUMBER.fullmatch(text):
        return None
    return float(text)


_EURO_AMOUNT_IN_TEXT = re.compile(
    r"(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d+)?)\s*(?:€|eur\b)",
    re.IGNORECASE,
)


def parse_first_amount(raw: str) -> int | None:
    """Первая сумма в строке: «750 € Kaltmiete», «Warmmiete 1.050 €»."""
    match = _EURO_AMOUNT_IN_TEXT.search(raw)
    if match is not None:
        return parse_amount(match.group(1))
    return parse_amount(raw)


def parse_amount(raw: str) -> int | None:
    """Разбирает денежную сумму и округляет её до целых евро.

    Округление арифметическое, а не встроенное round(): то округляет половину
    к чётному, из-за чего 1200,50 € превратилось бы в 1200.
    """
    value = parse_number(raw)
    # parse_number не принимает отрицательные значения, поэтому floor безопасен.
    return None if value is None else int(value + 0.5)


def parse_count(raw: str) -> int | None:
    """Разбирает целое количество: людей нельзя посчитать дробно."""
    value = parse_number(raw)
    if value is None or not value.is_integer():
        return None
    return int(value)


def parse_sqm(raw: str) -> float | None:
    """Разбирает площадь: «40», «40 m²», «Wohnfläche 74 m²»."""
    tagged = re.search(
        r"(\d+(?:[.,]\d+)?)\s*(?:m[²2]|qm)\b", raw, re.IGNORECASE
    )
    if tagged:
        return parse_number(tagged.group(1))
    return parse_number(_AREA_UNIT.sub("", raw))


def is_valid_city(name: str) -> bool:
    """Проверяет, что название города похоже на название, а не на набор символов."""
    if not CITY_MIN_LEN <= len(name) <= CITY_MAX_LEN:
        return False
    return bool(_CITY_ALLOWED.fullmatch(name))


def is_valid_name(name: str) -> bool:
    """Имя или фамилия: буквы любого алфавита, дефис и апостроф, без цифр."""
    if not NAME_MIN_LEN <= len(name) <= NAME_MAX_LEN:
        return False
    return bool(_NAME_ALLOWED.fullmatch(name))


def parse_search_radius(value: object) -> int:
    """Возвращает допустимый радиус или 0, если значение неизвестно."""
    try:
        radius = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return radius if radius in SEARCH_RADII else 0


def parse_applicant_gender(value: object) -> str | None:
    """'male' / 'female' или None, если значение пустое или неизвестно."""
    text = str(value or "").strip().lower()
    return text if text in APPLICANT_GENDERS else None


def parse_household_type(value: object) -> str | None:
    """Тип состава жильцов или None."""
    text = str(value or "").strip().lower()
    return text if text in HOUSEHOLD_TYPES else None


def area_is_too_small(minimum: object, area: object) -> bool:
    """Площадь заметно меньше запрошенного минимума (больше чем на 20%)."""
    try:
        min_val = float(minimum)  # type: ignore[arg-type]
        actual = float(area)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if min_val <= 0:
        return False
    return actual < min_val * (1 - AREA_UNDERSHOOT)


def primary_city_token(city: str) -> str:
    """«Lahr (Schwarzwald)» → «lahr», «Freiburg im Breisgau» → «freiburg»."""
    main = re.sub(r"\([^)]*\)", " ", city).strip()
    main = main.split(",")[0].strip()
    if not main:
        return city.strip().casefold()
    return re.split(r"[\s/\-]+", main)[0].casefold()


# Kleinanzeigen пишет «(ca. 50 km)», иногда с ca.. и неразрывными пробелами.
_DISTANCE_KM = re.compile(
    r"\(\s*(?:ca\.+|circa)?\s*(\d+(?:[.,]\d+)?)\s*km\s*\)",
    re.IGNORECASE,
)
_INVISIBLE = re.compile(r"[\u200b\u200c\u200d\ufeff\xa0]")


def _fold_place_text(text: str) -> str:
    """Убирает невидимые символы, из‑за которых «(ca. 50 km)» не узнавался."""
    return _INVISIBLE.sub(" ", text)


def parse_listing_distance_km(text: object) -> float | None:
    """Километры из пометки Kleinanzeigen «(ca. 50 km)», если она есть."""
    if text is None:
        return None
    match = _DISTANCE_KM.search(_fold_place_text(str(text)))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def infer_price_kind(
    *,
    warm: int | None = None,
    kalt: int | None = None,
    neben: int | None = None,
    label_hint: str | None = None,
    default: str = "unknown",
) -> str:
    """warm / kalt / unknown — по явным полям или подписи «Warmmiete» / «Kaltmiete»."""
    if label_hint:
        low = label_hint.casefold()
        if "warmmiete" in low:
            return "warm"
        if "kaltmiete" in low:
            return "kalt"
    if warm is not None:
        return "warm"
    if kalt is not None and neben is not None:
        return "warm"
    if kalt is not None:
        return "kalt"
    return default


def kalt_only_budget_reason(
    budget_max: object, price: object, price_kind: object
) -> str | None:
    """Отсекает Kaltmiete, которая уже почти равна бюджету Warmmiete."""
    if price_kind != "kalt":
        return None
    try:
        budget = int(budget_max)  # type: ignore[arg-type]
        rent = int(price)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if budget <= 0:
        return None
    buffer = max(KALT_BUDGET_BUFFER_MIN, int(budget * KALT_BUDGET_BUFFER_RATIO))
    threshold = budget - buffer
    if rent > threshold:
        return (
            f"указана только Kaltmiete {rent} € — при бюджете {budget} € Warmmiete "
            f"итог, скорее всего, превысит лимит"
        )
    return None


def city_mismatch_reason(
    profile: dict[str, object], apartment: dict[str, object]
) -> str | None:
    """Почему объявление вне зоны поиска, или None если город/радиус подходят."""
    city = str(profile.get("city_de") or profile.get("city") or "").strip()
    if not city:
        return None
    token = primary_city_token(city)
    if len(token) < 3:
        return None
    address = str(apartment.get("address") or "")
    title = str(apartment.get("title") or "")
    blob = _fold_place_text(f"{address} {title}").casefold()
    if not blob.strip():
        return None
    if token in blob:
        return None

    radius = parse_search_radius(profile.get("search_radius"))
    shown = address or title
    if radius <= 0:
        return f"город {shown} ≠ {city}"

    distance = apartment.get("distance_km")
    if distance is None:
        raw = apartment.get("raw_data")
        if isinstance(raw, dict):
            distance = raw.get("distance_km")
    if distance is None:
        distance = parse_listing_distance_km(address) or parse_listing_distance_km(title)
    try:
        km = float(distance) if distance is not None else None
    except (TypeError, ValueError):
        km = None
    if km is not None and km > radius:
        return f"расстояние {km:g} км > радиус {radius} км ({shown})"

    source = str(apartment.get("source") or "")
    if source == "wggesucht" and radius > 0:
        raw = apartment.get("raw_data")
        if isinstance(raw, dict) and raw.get("outside_search_radius"):
            return f"город {shown} вне радиуса {radius} км от {city}"
        listing_city = primary_city_token(shown)
        if listing_city and listing_city != token and km is None:
            allowed_names = raw.get("allowed_wg_city_names") if isinstance(raw, dict) else None
            if isinstance(allowed_names, list):
                allowed_tokens = {
                    primary_city_token(str(name)) for name in allowed_names if name
                }
                if listing_city not in allowed_tokens:
                    return f"город {shown} вне зоны {city} (+{radius} км)"
            else:
                return f"город {shown} вне зоны {city} (+{radius} км)"
    # Umkreis: соседи (Achern, Kippenheim) — ожидаемый результат поиска.
    # На странице объявления пометки «(N km)» уже нет, карточку не режем.
    return None
