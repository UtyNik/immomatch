"""Обращение к арендодателю в Anschreiben."""

from __future__ import annotations

import re
from typing import Any, Final

_COMPANY = re.compile(
    r"\b("
    r"GmbH|GmbH\s*&|AG|UG|KG|OHG|GbR|e\.?\s?K\.?|"
    r"Immobilien(?:service|verwaltung|gesellschaft)?|"
    r"Hausverwaltung|Wohnungsbau|Property|Gruppe|"
    r"Verwaltung(?:s)?(?:gesellschaft)?|"
    r"Gewerbe"
    r")\b",
    re.IGNORECASE,
)

_SALUTATION_OPENING = re.compile(
    r"^(?:Sehr geehrte[^\n,]*|Sehr geehrter[^\n,]*|Guten Tag[^\n,]*)\s*,?\s*",
    re.IGNORECASE,
)

# Простая эвристика для типичных имён — только если есть фамилия.
_FEMALE_FIRST_NAMES: Final[frozenset[str]] = frozenset(
    {
        "anna",
        "petra",
        "sabine",
        "monika",
        "andrea",
        "julia",
        "sandra",
        "claudia",
        "patricia",
        "maria",
        "susanne",
        "nicole",
        "katrin",
        "heike",
        "birgit",
        "christine",
        "silke",
        "martina",
        "barbara",
        "angelika",
    }
)
_MALE_FIRST_NAMES: Final[frozenset[str]] = frozenset(
    {
        "thomas",
        "michael",
        "andreas",
        "stefan",
        "peter",
        "markus",
        "martin",
        "christian",
        "daniel",
        "alexander",
        "klaus",
        "jürgen",
        "juergen",
        "frank",
        "bernd",
        "wolfgang",
        "hans",
        "max",
        "paul",
        "tim",
    }
)


def parse_kleinanzeigen_contact(
    text: str, *, commercial: bool = False
) -> dict[str, Any]:
    """Разбирает имя из блока контакта Kleinanzeigen."""
    cleaned = " ".join(text.split())
    for marker in ("Gewerblicher Nutzer", "Privater Nutzer", "Aktiv seit"):
        if marker in cleaned:
            cleaned = cleaned.split(marker, 1)[0].strip()

    kind = "unknown"
    first_name: str | None = None
    last_name: str | None = None

    if commercial or _COMPANY.search(cleaned):
        kind = "company"
    elif " - " in cleaned:
        left, right = cleaned.split(" - ", 1)
        if _COMPANY.search(left):
            kind = "company"
        else:
            kind = "person"
            first_name, last_name = _split_person_name(right.strip() or left.strip())
    else:
        first_name, last_name = _split_person_name(cleaned)
        if last_name:
            kind = "person"
        elif first_name:
            kind = "person"

    gender = _guess_gender(first_name) if kind == "person" else None
    return {
        "kind": kind,
        "display_name": cleaned,
        "first_name": first_name,
        "last_name": last_name,
        "gender": gender,
    }


def _split_person_name(name: str) -> tuple[str | None, str | None]:
    parts = [part for part in name.split() if part]
    if len(parts) >= 2:
        return parts[0], parts[-1]
    if len(parts) == 1:
        return parts[0], None
    return None, None


def _guess_gender(first_name: str | None) -> str | None:
    if not first_name:
        return None
    token = first_name.casefold()
    if token in _FEMALE_FIRST_NAMES:
        return "female"
    if token in _MALE_FIRST_NAMES:
        return "male"
    return None


def build_salutation(contact: dict[str, Any] | None) -> str:
    """Формирует обращение для немецкого Anschreiben."""
    if not contact:
        return "Sehr geehrte Damen und Herren,"

    kind = str(contact.get("kind") or "unknown")
    if kind == "company":
        return "Sehr geehrte Damen und Herren,"

    last_name = str(contact.get("last_name") or "").strip()
    first_name = str(contact.get("first_name") or "").strip()
    gender = contact.get("gender")

    if last_name:
        if gender == "male":
            return f"Sehr geehrter Herr {last_name},"
        if gender == "female":
            return f"Sehr geehrte Frau {last_name},"
        return f"Guten Tag, Herr/Frau {last_name},"

    if first_name:
        return "Guten Tag,"

    return "Sehr geehrte Damen und Herren,"


def salutation_from_listing(apartment: dict[str, Any]) -> str:
    """Обращение из полей объявления."""
    contact = apartment.get("landlord_contact")
    if isinstance(contact, dict):
        return build_salutation(contact)
    return build_salutation(None)


def apply_letter_salutation(letter: str, salutation: str) -> str:
    """Подставляет нужное обращение в начало письма."""
    if not letter.strip():
        return letter
    body = letter.strip()
    if _SALUTATION_OPENING.match(body):
        body = _SALUTATION_OPENING.sub(f"{salutation}\n\n", body, count=1)
    else:
        body = f"{salutation}\n\n{body}"
    return body.strip()
