"""Нормализация имени и города: кириллица → латиница / официальный немецкий."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Final

from services.ai_agent import _ask

logger = logging.getLogger(__name__)

MAX_TRANSLATE_TOKENS: Final[int] = 200

_CYRILLIC = re.compile(r"[а-яёіїєґА-ЯЁІЇЄҐ]")

# Частые немецкие города: ключ — lower без диакритики и без ь/ъ.
_CITY_ALIASES: Final[dict[str, str]] = {
    "munchen": "München",
    "muenchen": "München",
    "munich": "München",
    "мюнхен": "München",
    "berlin": "Berlin",
    "берлин": "Berlin",
    "берлін": "Berlin",
    "hamburg": "Hamburg",
    "гамбург": "Hamburg",
    "koln": "Köln",
    "koeln": "Köln",
    "cologne": "Köln",
    "кельн": "Köln",
    "frankfurt": "Frankfurt",
    "франкфурт": "Frankfurt",
    "stuttgart": "Stuttgart",
    "штутгарт": "Stuttgart",
    "dusseldorf": "Düsseldorf",
    "duesseldorf": "Düsseldorf",
    "дюссельдорф": "Düsseldorf",
    "leipzig": "Leipzig",
    "лейпциг": "Leipzig",
    "dortmund": "Dortmund",
    "дортмунд": "Dortmund",
    "essen": "Essen",
    "эссен": "Essen",
    "ессен": "Essen",
    "bremen": "Bremen",
    "бремен": "Bremen",
    "dresden": "Dresden",
    "дрезден": "Dresden",
    "hannover": "Hannover",
    "ганновер": "Hannover",
    "nurnberg": "Nürnberg",
    "nuernberg": "Nürnberg",
    "nuremberg": "Nürnberg",
    "нюрнберг": "Nürnberg",
    "duisburg": "Duisburg",
    "дуйсбург": "Duisburg",
    "bochum": "Bochum",
    "бохум": "Bochum",
    "wuppertal": "Wuppertal",
    "вупперталь": "Wuppertal",
    "bielefeld": "Bielefeld",
    "билефельд": "Bielefeld",
    "bonn": "Bonn",
    "бонн": "Bonn",
    "munster": "Münster",
    "muenster": "Münster",
    "мюнстер": "Münster",
    "karlsruhe": "Karlsruhe",
    "карлсруэ": "Karlsruhe",
    "карлсруе": "Karlsruhe",
    "mannheim": "Mannheim",
    "маннхайм": "Mannheim",
    "мангейм": "Mannheim",
    "augsburg": "Augsburg",
    "аугсбург": "Augsburg",
    "wiesbaden": "Wiesbaden",
    "висбаден": "Wiesbaden",
    "gelsenkirchen": "Gelsenkirchen",
    "гельзенкирхен": "Gelsenkirchen",
    "monchengladbach": "Mönchengladbach",
    "moenchengladbach": "Mönchengladbach",
    "менхенгладбах": "Mönchengladbach",
    "braunschweig": "Braunschweig",
    "брауншвейг": "Braunschweig",
    "chemnitz": "Chemnitz",
    "хемниц": "Chemnitz",
    "kiel": "Kiel",
    "киль": "Kiel",
    "aachen": "Aachen",
    "ахен": "Aachen",
    "halle": "Halle",
    "халле": "Halle",
    "magdeburg": "Magdeburg",
    "магдебург": "Magdeburg",
    "freiburg": "Freiburg",
    "фрайбург": "Freiburg",
    "фрайбурґ": "Freiburg",
    "krefeld": "Krefeld",
    "крефельд": "Krefeld",
    "lubeck": "Lübeck",
    "luebeck": "Lübeck",
    "любек": "Lübeck",
    "oberhausen": "Oberhausen",
    "оберхаузен": "Oberhausen",
    "erfurt": "Erfurt",
    "эрфурт": "Erfurt",
    "ерфурт": "Erfurt",
    "mainz": "Mainz",
    "майнц": "Mainz",
    "rostock": "Rostock",
    "росток": "Rostock",
    "kassel": "Kassel",
    "кассель": "Kassel",
    "hagen": "Hagen",
    "хаген": "Hagen",
    "saarbrucken": "Saarbrücken",
    "saarbruecken": "Saarbrücken",
    "саарбрюккен": "Saarbrücken",
    "саарбрюкен": "Saarbrücken",
    "hamm": "Hamm",
    "хамм": "Hamm",
    "potsdam": "Potsdam",
    "потсдам": "Potsdam",
    "ludwigshafen": "Ludwigshafen",
    "людвигсхафен": "Ludwigshafen",
    "oldenburg": "Oldenburg",
    "ольденбург": "Oldenburg",
    "osnabruck": "Osnabrück",
    "osnabrueck": "Osnabrück",
    "оснабрюк": "Osnabrück",
    "leverkusen": "Leverkusen",
    "леверкузен": "Leverkusen",
    "heidelberg": "Heidelberg",
    "гейдельберг": "Heidelberg",
    "хайдельберг": "Heidelberg",
    "solingen": "Solingen",
    "золинген": "Solingen",
    "darmstadt": "Darmstadt",
    "дармштадт": "Darmstadt",
    "regensburg": "Regensburg",
    "регенсбург": "Regensburg",
    "ingolstadt": "Ingolstadt",
    "ингольштадт": "Ingolstadt",
    "wurzburg": "Würzburg",
    "wuerzburg": "Würzburg",
    "вюрцбург": "Würzburg",
    "offenbach": "Offenbach",
    "оффенбах": "Offenbach",
    "ulm": "Ulm",
    "ульм": "Ulm",
    "pforzheim": "Pforzheim",
    "пфорцхайм": "Pforzheim",
    "wolfsburg": "Wolfsburg",
    "вольфсбург": "Wolfsburg",
    "gottingen": "Göttingen",
    "goettingen": "Göttingen",
    "геттинген": "Göttingen",
    "гёттинген": "Göttingen",
    "bottrop": "Bottrop",
    "ботроп": "Bottrop",
    "trier": "Trier",
    "трир": "Trier",
    "reutlingen": "Reutlingen",
    "ройтлинген": "Reutlingen",
    "bremerhaven": "Bremerhaven",
    "бремерхафен": "Bremerhaven",
    "koblenz": "Koblenz",
    "кобленц": "Koblenz",
    "bergisch gladbach": "Bergisch Gladbach",
    "erlangen": "Erlangen",
    "эрланген": "Erlangen",
    "ерланген": "Erlangen",
    "moers": "Moers",
    "мёрс": "Moers",
    "мерс": "Moers",
    "siegen": "Siegen",
    "зиген": "Siegen",
    "hildesheim": "Hildesheim",
    "хильдесхайм": "Hildesheim",
    "salzgitter": "Salzgitter",
    "зальцгиттер": "Salzgitter",
    "cottbus": "Cottbus",
    "котбус": "Cottbus",
    "kaiserslautern": "Kaiserslautern",
    "кайзерслаутерн": "Kaiserslautern",
    "gutersloh": "Gütersloh",
    "guetersloh": "Gütersloh",
    "гютерсло": "Gütersloh",
    "schwerin": "Schwerin",
    "шверин": "Schwerin",
    "witten": "Witten",
    "виттен": "Witten",
    "gera": "Gera",
    "гера": "Gera",
    "lahr": "Lahr",
    "лар": "Lahr",
    "offenburg": "Offenburg",
    "оффенбург": "Offenburg",
    "konstanz": "Konstanz",
    "констанц": "Konstanz",
    "baden-baden": "Baden-Baden",
    "баден-баден": "Baden-Baden",
}

# Украинская практическая транслитерация: «и» → y (Микита → Mykyta).
_TRANSLIT: Final[dict[str, str]] = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e",
    "є": "ie", "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch", "ь": "", "ъ": "", "ю": "iu", "я": "ia",
    "ы": "y", "э": "e", "ё": "io",
}

_PROMPT: Final[str] = """\
You normalize a tenant's name and city for German housing search.

Return JSON only:
{
  "first_name_latin": "...",
  "last_name_latin": "...",
  "city_de": "..."
}

Rules:
- Given name and surname: Latin letters only. Ukrainian/Russian → a common
  Latin spelling (Микита → Mykyta, Никита → Nikita, Литвинов → Lytvynov).
  If already Latin, keep it and only fix capitalization (Anna, Müller).
- city_de: the official German place name with umlauts as used on
  kleinanzeigen.de (Мюнхен → München, Кельн → Köln, Фрайбург → Freiburg,
  Нюрнберг → Nürnberg, Лар → Lahr). Keep a district if the user named one
  (Berlin-Mitte). Do not replace the city with a different one.
- Empty input stays an empty string.
"""


def _fold_city_key(text: str) -> str:
    """Ключ для словаря городов: нижний регистр, без диакритики латиницы."""
    folded = text.strip().casefold()
    for source, target in (
        ("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss"),
        ("é", "e"), ("è", "e"),
    ):
        folded = folded.replace(source, target)
    return re.sub(r"\s+", " ", folded)


def _title_latin(text: str) -> str:
    """Anna-Maria, O'Connor — каждое слово с заглавной."""
    parts = re.split(r"([\s'’\-]+)", text.strip())
    return "".join(part[:1].upper() + part[1:].lower() if part.isalpha() else part for part in parts)


def _transliterate(text: str) -> str:
    """Запасной вариант без модели: кириллица в латиницу по буквам."""
    chars: list[str] = []
    for char in text.strip():
        lower = char.casefold()
        if lower in _TRANSLIT:
            mapped = _TRANSLIT[lower]
            chars.append(mapped[:1].upper() + mapped[1:] if char.isupper() else mapped)
        else:
            chars.append(char)
    return _title_latin("".join(chars))


def _fallback_name(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if _CYRILLIC.search(text):
        return _transliterate(text)
    return _title_latin(text)


def _fallback_city(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    mapped = _CITY_ALIASES.get(_fold_city_key(text))
    if mapped:
        return mapped
    if _CYRILLIC.search(text):
        return _transliterate(text)
    return _title_latin(text)


def _local_normalize(first_name: str, last_name: str, city: str) -> dict[str, str]:
    """Словарь городов и транслитерация — если модель недоступна."""
    return {
        "first_name_latin": _fallback_name(first_name),
        "last_name_latin": _fallback_name(last_name),
        "city_de": _fallback_city(city),
    }


def _needs_model(first_name: str, last_name: str, city: str) -> bool:
    """Модель нужна, если есть кириллица или город не из словаря."""
    if _CYRILLIC.search(f"{first_name} {last_name} {city}"):
        return True
    if city.strip() and _fold_city_key(city) not in _CITY_ALIASES:
        return True
    return False


def _clean_result(parsed: dict[str, Any], fallback: dict[str, str]) -> dict[str, str]:
    """Берёт поля модели, пустые заменяет локальным запасным вариантом."""
    result: dict[str, str] = {}
    for key in ("first_name_latin", "last_name_latin", "city_de"):
        value = str(parsed.get(key) or "").strip()
        result[key] = value or fallback[key]
    return result


async def normalize_and_translate_user_input(
    first_name: str,
    last_name: str,
    city: str,
) -> dict[str, str]:
    """Латинские имя/фамилия и официальное немецкое название города.

    При сбое OpenAI возвращает локальный запасной вариант, анкету не роняет.
    """
    fallback = _local_normalize(first_name, last_name, city)
    if not (first_name.strip() or last_name.strip() or city.strip()):
        return fallback
    if not _needs_model(first_name, last_name, city):
        return fallback

    payload = {
        "first_name": first_name.strip(),
        "last_name": last_name.strip(),
        "city": city.strip(),
    }
    try:
        parsed = await _ask(
            _PROMPT,
            json.dumps(payload, ensure_ascii=False),
            MAX_TRANSLATE_TOKENS,
            "identity",
        )
    except RuntimeError as error:
        logger.warning("Нормализация имени/города через модель не удалась: %s", error)
        return fallback

    cleaned = _clean_result(parsed, fallback)
    logger.info(
        "Нормализация: %r %r / %r → %s %s / %s",
        first_name,
        last_name,
        city,
        cleaned["first_name_latin"],
        cleaned["last_name_latin"],
        cleaned["city_de"],
    )
    return cleaned
