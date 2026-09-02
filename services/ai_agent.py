"""AI-оценка объявления и генерация немецкого Anschreiben.

Работа разбита на два обращения к модели: сначала короткое решение «можно ли
подавать заявку», затем письмо — и только для подошедших объявлений. Одним
запросом модель писала письмо одновременно с оценкой и печатала вердикт раньше,
чем рассуждение, из-за чего отказывала вопреки собственному объяснению.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any, Final

from openai import APIError, AsyncOpenAI

from config import get_settings
from texts import DEFAULT_LANG
from validators import area_below_minimum, area_is_too_small, city_mismatch_reason, kalt_only_budget_reason, parse_search_radius
from services.salutation import apply_letter_salutation, salutation_from_listing

logger = logging.getLogger(__name__)

MODEL: Final[str] = "gpt-4o-mini"
REQUEST_TIMEOUT: Final[float] = 60.0
# Длинные объявления обрезаем: смысл сохраняется, а расход токенов ограничен.
MAX_DESCRIPTION_CHARS: Final[int] = 4000
# Решение — это несколько полей JSON, письма в нём нет.
MAX_DECISION_TOKENS: Final[int] = 400
# Письму на 150 слов хватает ~400 токенов, остальное — запас на разметку.
MAX_LETTER_TOKENS: Final[int] = 700

_LANGUAGE_NAMES: Final[dict[str, str]] = {
    "ua": "Ukrainian",
    "ru": "Russian",
    "en": "English",
}

_DECISION_PROMPT: Final[str] = """\
You are a filter for apartment hunters in Germany.

You receive a tenant profile and one listing (German text). Decide whether the
tenant is allowed to apply. You are not a matchmaker: the tenant judges for
himself whether he likes the flat, your only job is to drop offers he cannot
rent at all. When in doubt, the answer is "match": true.

Reject the listing only in these cases:
- the warm rent clearly exceeds the budget. listing.price_eur is Warmmiete
  (Kaltmiete + Nebenkosten when both are stated). Do not treat Kaltmiete
  alone as the monthly cost;
- there are fewer rooms than the tenant needs, or the floor area is more
  than 20% below sqm_min or above sqm_max, or (only if search_radius_km
  is 0) the flat is in another city.
  If search_radius_km > 0, nearby towns inside that Umkreis are valid:
  Achern, Offenburg, Kippenheim next to Lahr must NOT be rejected for city;
- the tenant has pets and the listing forbids them ("keine Haustiere");
- the listing is gender-restricted against this tenant. Use applicant_gender:
  "nur für Frauen", "nur Damen", "nur Studentinnen", "Frauen-WG",
  "keine Männer", "Nachmieterin", "Mieterin gesucht", "weibliche Nachmieterin"
  block applicant_gender "male";
  "nur für Männer", "nur Herren", "Männer-WG", "keine Frauen",
  "Nachmieter gesucht" only when it clearly excludes women, block
  applicant_gender "female". Mixed wording ("Frauen und Männer",
  "Studentinnen und Studenten") is not a blocker.
  "Nachmieterin gesucht" is women-only. Generic "Nachmieter gesucht" is not;
- the listing takes fewer people than the tenant's household. Compare the
  numbers and nothing else: "für 1 Person" blocks a household of two,
  "maximal 2 Personen" does not, "maximal 3 Personen" does not.
  "nur Singles oder Paare" and "Paare oder kleine Familien" match a
  household of two. "keine Familien" blocks household_type "family"
  or household_size >= 3.
  Never reject two people because "they should know each other" or because
  the landlord prefers a couple — two is a couple-sized household;
- the listing demands a WBS the tenant does not have: "nur mit WBS",
  "WBS erforderlich", "Wohnberechtigungsschein erforderlich" while
  has_wbs is false;
- the listing refuses Jobcenter / social benefits while uses_jobcenter is
  true: "kein Jobcenter", "keine Jobcenter", "nur Berufstätige",
  "keine Transferleistungen", "nur Selbstzahler",
  "keine Bürgergeldempfänger";
- it is not an ordinary long-term rental of a whole flat. Always reject:
  "Tauschwohnung", "Tauschangebot", "zum Tausch";
  "Ferienwohnung", "Ferienhaus", "Fewo", "Urlaubswohnung", "pro Nacht",
  "Tagesmiete", "Airbnb", "Monteurswohnung";
  "Zwischenmiete" or a stay limited to a few months; "nur Gewerbe";
  an ad that searches for a flat ("Suche eine Wohnung") instead of offering
  one; a WG room ("WG-Zimmer", "Zimmer in Frauen-WG",
  "Mitbewohnerin gesucht").
  If household_type is not "wg", also reject a whole flat rented as a WG
  with a price per person — "WG Vermietung", "als WG",
  "Kaltmiete pro Person", "Warmmiete pro Person", "pro Kopf".
  "WG-geeignet" alone is not a blocker: many ordinary flats tick that box.
- the tenant's own notes state a requirement the listing clearly violates.

Three rules override everything else:

1. Never infer what the profile does not state. Age, occupation, nationality
   and smoking are unknown, and a restriction about them blocks nothing.
   applicant_gender and household_type ARE known when present: compare them
   with the listing. household_size is the number of people moving in.
   household_type "family" is blocked by "keine Familien" even for two people.
   household_type "partner_female" / "partner_male" is a couple, not a WG.

2. WBS and Jobcenter are independent facts. Having a WBS does not imply
   Jobcenter, and Jobcenter does not imply a WBS. A WBS is a qualification,
   never a drawback: if the listing requires a WBS and has_wbs is true, that
   is a strong match. If the listing says nothing about WBS, ignore has_wbs.
   If the listing says nothing about Jobcenter or social benefits, ignore
   uses_jobcenter. Never treat WBS as a reason to refuse a private listing.

3. A move-in date in the future is normal for a rental. "ab 01.11.", "Erstbezug
   nach Sanierung", "verfügbar ab Januar" — none of these block anything.

4. search_radius_km is the tenant's Umkreis. When it is greater than 0, a
   different town than tenant.city is not a blocker.

Answer with JSON only, exactly these keys, in this order:
{{
  "blocking_phrase": "the exact sentence copied from the listing that makes
      renting impossible for THIS tenant, or an empty string if there is none",
  "blocking_fact": "which piece of the tenant profile that sentence
      contradicts. Use exactly one of: city, budget, rooms, area, household,
      gender, pets, wbs, jobcenter, notes, not_a_rental. Use an empty string if nothing
      is contradicted",
  "match": boolean, false only when both fields above are filled,
  "reason": "one or two sentences in {language}, explaining the decision"
}}

Fill the first two fields before deciding: the verdict follows from them.
A sentence blocks only if it contradicts something the profile actually states.
"Keine Haustiere" is no blocker for a tenant without pets, and "nur Singles
oder Paare" is no blocker for a household of two. "Maximal 2 Personen" is no
blocker for two people. "Nur mit WBS" is no blocker when has_wbs is true. "Kein Jobcenter" / "nur Berufstätige" is no blocker
when uses_jobcenter is false. "Nur für Frauen" is no blocker when
applicant_gender is female. A floor area mentioned only in the description
("Wohnfläche 32 m²", "ca. 90 qm") blocks the listing when it is more than
20% below sqm_min (28 m² vs 35 m² is not a blocker: that is a typical
1,5-Zimmer size) or, if sqm_max is set, above sqm_max. If the listing does
not state an area, do not reject on area.
"""

_LETTER_PROMPT: Final[str] = """\
You write German cover letters (Anschreiben) for apartment hunters.

You receive a tenant profile and a listing the tenant wants to apply for.
Answer with JSON only: {{"anschreiben": "..."}}

The letter must be polite, flawless German of 80-150 words.

Start the letter with listing.salutation exactly as given in the JSON payload.
Do not replace it with another greeting. Sign it exactly:

Mit freundlichen Grüßen,
{first_name} {last_name}

Use the tenant's first_name and last_name from the profile. Never sign with a
Telegram username, never invent a name, never omit the surname if it is given.

German grammar is mandatory and follows applicant_gender and household_type:
- male: "ich bin ein ruhiger Mieter", "ein zuverlässiger Mieter". If you
  mention age, only when the profile contains it: "ein 23-jähriger …".
  Never invent an age.
- female: "ich bin eine ruhige Mieterin", "eine zuverlässige Mieterin".
  Age form: "eine 23-jährige …" only if age is in the profile.
- household_type "partner_female": the other person is always
  "meine Partnerin" or "meine Freundin". Never "mein Partner".
- household_type "partner_male": always "mein Partner" or "mein Freund".
  Never "meine Partnerin".
- household_type "family": mention that you are moving in as a family
  with children only as a fact of household_size, without inventing names
  or ages of the children.
- household_type "single": you move in alone.
- household_type "wg": joint rent with friends of a whole flat, not an
  application for a WG-Zimmer.

Watch articles, cases and declensions: never write "ich bin ruhiger Mieter".

Mention only facts given in the profile, and only those that help:
- net income is OPTIONAL. tenant.net_income_eur may be a number or null.
  By default NEVER write a concrete income, salary, Nettoeinkommen or
  Gehalt. Ordinary ads do not need this.
  Mention a number ONLY if listing.income_proof_requested is true AND
  tenant.net_income_eur is a number. Then write one polite sentence, e.g.
  "Ein geregeltes monatliches Nettoeinkommen von ca. X € ist vorhanden
  und kann nachgewiesen werden." Use exactly that number. Never invent
  another amount.
  If income_proof_requested is true but net_income_eur is null: do not
  invent a figure. Offer documents on request, e.g. "Gerne reiche ich
  auf Anfrage alle erforderlichen Unterlagen (SCHUFA, Nachweise) nach."
  If income_proof_requested is false: say nothing about income, salary
  or Einkommensnachweis, even when net_income_eur is known.
- name how many people are moving in whenever the listing asks for the
  "Personenanzahl" or limits the number of tenants;
- never write a word about WBS unless the listing itself asks for a
  Wohnberechtigungsschein;
- never write a word about Jobcenter, Bürgergeld or any social support
  unless the listing itself asks about it: with private landlords it costs
  the tenant the viewing, and it is nobody's business at this stage;
- mention pets only if the tenant has them or the listing asks;
- never list what the tenant lacks, and never apologise for it.
Adapt to the listing: if the landlord wants quiet tenants, say the tenant is
quiet; if documents are requested, offer to bring them; if the ad asks for
specific details, promise them in the reply.
Never invent a job title, household size, family details or references that
the profile does not contain.
"""

# Причины отказа, которые мы принимаем. Возраст и профессия анкетой не
# покрыты — такие отказы отменяются. «keine Familien» оставляем только
# для household_type "family" (см. _reject_is_invalid).
_BLOCKING_FACTS: Final[frozenset[str]] = frozenset(
    {
        "city",
        "budget",
        "rooms",
        "area",
        "household",
        "gender",
        "pets",
        "wbs",
        "jobcenter",
        "notes",
        "not_a_rental",
    }
)
# Причины, которые модель обязана подтвердить цитатой из объявления. Город,
# цену и комнаты она берёт из полей, там цитировать нечего.
_PHRASE_FACTS: Final[frozenset[str]] = frozenset(
    {"area", "household", "gender", "pets", "wbs", "jobcenter", "notes", "not_a_rental"}
)
_NON_WORD = re.compile(r"[^a-zäöüß0-9]+")
_SIGN_OFF = re.compile(
    r"\n*mit\s+freundlichen\s+gr[uü](?:ss|ß)en[,.]?\s*[\s\S]*$",
    re.IGNORECASE,
)
# Комната в WG — бот ищет целую квартиру, это всегда отказ.
_WG_ROOM = re.compile(
    r"wg[\s\-]*zimmer|"
    r"zimmer\s+in\s+(?:einer\s+)?(?:frauen|männer|herren|damen)?[\s\-]*wg|"
    r"mitbewohner(?:in)?\s+gesucht",
    re.IGNORECASE,
)
# Квартира сдаётся как WG / цена за человека. Не путать с галочкой «WG-geeignet».
_WG_FLAT = re.compile(
    r"wg[\s\-]*vermietung|"
    r"als\s+(?:eine[rn]?\s+)?wg\b|"
    r"zweck[\s\-]*wg|"
    r"(?:kalt|warm)?miete\s+pro\s+(?:person|kopf)|"
    r"pro\s+(?:person|kopf)|je\s+person",
    re.IGNORECASE,
)
_SWAP_LISTING = re.compile(
    r"tauschwohnung|tauschangebot|tauschwohnung\.com|"
    r"zum\s+tausch|nur\s+tauschangebote|"
    r"tausche\s+(?:meine?\s+)?(?:\d|wohnung)|wohnung\s+zum\s+tausch",
    re.IGNORECASE,
)
_HOLIDAY_LISTING = re.compile(
    r"ferienwohnung|ferienhaus|ferienvermietung|\bfewo\b|"
    r"urlaubswohnung|urlaubsvermietung|"
    r"pro\s+nacht|je\s+nacht|/nacht|pro\s+tag|tagesmiete|"
    r"airbnb|monteurs?(?:zimmer|wohnung|unterkunft)",
    re.IGNORECASE,
)
_SHORT_STAY = re.compile(
    r"zwischenmiete|zwischenvermietung|"
    r"zeitmiete|kurzzeit(?:miete|vermietung)",
    re.IGNORECASE,
)
_NOT_OFFERING = re.compile(
    r"wohnungsgesuch|"
    r"(?:^|[\s:—\-])(?:ich\s+)?suche(?:\s+(?:mir|eine|ein))?\s+(?:eine?\s+)?(?:miet)?wohnung|"
    r"(?:^|[\s:—\-])suche(?:\s+(?:mir|eine|ein))?\s+(?:eine?\s+)?(?:miet)?wohnung|"
    r"auf der suche nach (?:einer?\s+)?(?:miet)?wohnung|"
    r"(?:^|[\s:—\-])gesuch[\s:—\-]|"
    r"(?:suche|gesucht)\s+(?:eine?\s+)?(?:miet)?wohnung|"
    r"wohnung\s+gesucht|"
    r"zimmer\s+gesucht|"
    r"sucht (?:eine|einen|mir)\s+(?:miet)?wohnung|"
    r"nur\s+gewerbe",
    re.IGNORECASE,
)
_WANTED_TITLE = re.compile(
    r"wohnungsgesuch|wohnung\s+gesucht|zimmer\s+gesucht|"
    r"^(?:ich\s+)?suche\b|"
    r"\bgesuch\b",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def _client() -> AsyncOpenAI:
    """Клиент OpenAI создаётся один раз: внутри живёт пул HTTP-соединений,
    а новый экземпляр на каждый запрос оставлял бы сокеты незакрытыми."""
    settings = get_settings()
    return AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=REQUEST_TIMEOUT,
    )


def apply_letter_signature(letter: str, profile: dict[str, Any]) -> str:
    """Подпись строго «Mit freundlichen Grüßen,\\nИмя Фамилия», без ника Telegram."""
    first = str(profile.get("first_name") or "").strip()
    last = str(profile.get("last_name") or "").strip()
    name = " ".join(part for part in (first, last) if part) or "Interessent"
    closing = f"Mit freundlichen Grüßen,\n{name}"
    body = _SIGN_OFF.sub("", letter.strip()).rstrip()
    if not body:
        return closing
    return f"{body}\n\n{closing}"


# Явный запрос финансовой состоятельности в объявлении — не путать с
# обычным «Berufstätige» без требования показать доход.
_INCOME_PROOF_ASK = re.compile(
    r"einkommensnachweis(?:e|es)?|"
    r"einkommensbescheinigung|"
    r"gehaltsnachweis(?:e|es)?|"
    r"verdienstnachweis|"
    r"einkommen\s+nachweisen|"
    r"nachweis(?:e)?\s+(?:über|zum|des|der)\s+"
    r"(?:das\s+)?(?:einkommen|gehalt|einkünfte|bonität)|"
    r"\bsolvent\b|"
    r"\bgehalt\b|"
    r"berufstätige\s+mit\s+geregeltem\s+einkommen|"
    r"geregeltes?\s+(?:netto)?einkommen|"
    r"nettoeinkommen|"
    r"einkommenshöhe|"
    r"mindesteinkommen|"
    r"bonitätsnachweis|"
    r"(?:3|drei)\s*[x×]\s*(?:netto)?(?:kalt)?miete|"
    r"dreifache(?:s|n)?\s+(?:netto)?(?:einkommen|kaltmiete|miete)|"
    r"zahlungskräftig|"
    r"finanziell\s+(?:abgesichert|leistungsfähig)",
    re.IGNORECASE,
)
# Предложения, которые выдают сумму или сам факт дохода.
_INCOME_MENTION = re.compile(
    r"nettoeinkommen|\bnetto\s+einkommen\b|\beinkommen\b|\bgehalt\b|"
    r"einkünfte|\bverdienst\b|\bsolvent\b|bonität|"
    r"einkommensnachweis|gehaltsnachweis",
    re.IGNORECASE,
)
_INCOME_PROOF_SENTENCE: Final[str] = (
    "Ein geregeltes monatliches Nettoeinkommen von ca. {amount} € "
    "ist vorhanden und kann nachgewiesen werden."
)
_DOCUMENTS_OFFER_SENTENCE: Final[str] = (
    "Gerne reiche ich auf Anfrage alle erforderlichen Unterlagen "
    "(SCHUFA, Nachweise) nach."
)


def _optional_net_income(value: Any) -> int | None:
    """Чистый доход из анкеты: целое евро или None, если поле пропущено."""
    if value is None or value == "":
        return None
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return None
    return amount if amount >= 0 else None


def listing_requests_income_proof(apartment: dict[str, Any]) -> bool:
    """Просит ли объявление подтвердить доход / платёжеспособность."""
    blob = " ".join(
        str(apartment.get(key) or "")
        for key in ("title", "description", "address")
    )
    return bool(_INCOME_PROOF_ASK.search(blob))


def _insert_before_signoff(letter: str, sentence: str) -> str:
    """Вставляет предложение перед подписью, если его ещё нет в тексте."""
    if sentence.casefold() in letter.casefold():
        return letter
    match = _SIGN_OFF.search(letter)
    if not match:
        return f"{letter.rstrip()}\n\n{sentence}"
    body = letter[: match.start()].rstrip()
    closing = letter[match.start() :].lstrip()
    return f"{body}\n\n{sentence}\n\n{closing}"


_ABBREVIATION_DOT = re.compile(
    r"\b(?:ca|z\.B|bzw|inkl|ggf|usw|etc|Nr|Dr)\.",
    re.IGNORECASE,
)


def _drop_income_sentences(letter: str) -> str:
    """Убирает предложения про доход, оставляя остальной текст письма."""
    # «ca. 3200 €» иначе режется по точке в «ca.» и сумма остаётся сиротой.
    placeholders: list[str] = []

    def _protect(match: re.Match[str]) -> str:
        placeholders.append(match.group(0))
        return f"\x00{len(placeholders) - 1}\x00"

    def _restore(text: str) -> str:
        return re.sub(
            r"\x00(\d+)\x00",
            lambda match: placeholders[int(match.group(1))],
            text,
        )

    protected = _ABBREVIATION_DOT.sub(_protect, letter.strip())
    chunks = re.split(r"(?<=[.!?])\s+", protected)
    kept = [_restore(chunk) for chunk in chunks if not _INCOME_MENTION.search(_restore(chunk))]
    text = " ".join(kept)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def apply_letter_income_policy(
    letter: str, profile: dict[str, Any], apartment: dict[str, Any]
) -> str:
    """Доход в письме только если объявление само просит подтверждение."""
    if not letter.strip():
        return letter
    asked = listing_requests_income_proof(apartment)
    income = _optional_net_income(profile.get("net_income"))

    if not asked:
        return _drop_income_sentences(letter)

    if income is not None:
        if str(income) not in letter and not re.search(
            r"nettoeinkommen", letter, re.IGNORECASE
        ):
            return _insert_before_signoff(
                letter, _INCOME_PROOF_SENTENCE.format(amount=income)
            )
        return letter

    cleaned = _drop_income_sentences(letter)
    if not re.search(r"schufa|nachweis", cleaned, re.IGNORECASE):
        cleaned = _insert_before_signoff(cleaned, _DOCUMENTS_OFFER_SENTENCE)
    return cleaned


def _build_payload(user_profile: dict[str, Any], apartment: dict[str, Any]) -> str:
    """Готовит компактное описание задачи для модели."""
    description = str(apartment.get("description") or "")
    if len(description) > MAX_DESCRIPTION_CHARS:
        description = description[:MAX_DESCRIPTION_CHARS] + "…"

    tenant = {
        "first_name": user_profile.get("first_name") or "",
        "last_name": user_profile.get("last_name") or "",
        "applicant_gender": user_profile.get("applicant_gender"),
        "city": user_profile.get("city_de") or user_profile.get("city"),
        "search_radius_km": parse_search_radius(user_profile.get("search_radius")),
        "budget_max_warm_eur": user_profile.get("budget_max"),
        "rooms_min": user_profile.get("rooms_min"),
        "sqm_min": user_profile.get("sqm_min"),
        "sqm_max": user_profile.get("sqm_max"),
        "household_size": user_profile.get("household_size"),
        "household_type": user_profile.get("household_type"),
        "has_wbs": user_profile.get("has_wbs"),
        "uses_jobcenter": user_profile.get("uses_jobcenter"),
        "has_pets": user_profile.get("has_pets"),
        "notes": user_profile.get("custom_notes"),
    }
    net_income = _optional_net_income(user_profile.get("net_income"))
    salutation = salutation_from_listing(apartment)

    payload = {
        # Пустые поля выбрасываем: пара «ключ: null» подталкивает модель
        # додумывать значение, а не считать его неизвестным.
        "tenant": {
            key: value
            for key, value in tenant.items()
            if value is not None and value != ""
        },
        "listing": {
            "title": apartment.get("title"),
            "price_eur": apartment.get("price"),
            "price_kind": apartment.get("price_kind"),
            "rooms": apartment.get("rooms"),
            "area_m2": apartment.get("sqm"),
            "address": apartment.get("address"),
            "distance_km": apartment.get("distance_km"),
            "description": description,
            "income_proof_requested": listing_requests_income_proof(apartment),
            "landlord_contact": apartment.get("landlord_contact"),
            "salutation": salutation,
        },
    }
    # Доход опционален: null должен дойти до модели, иначе она выдумает сумму.
    payload["tenant"]["net_income_eur"] = net_income
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _extract_json(raw: str) -> dict[str, Any]:
    """Разбирает ответ модели, переживая обёртку в ```json ... ```."""
    text = raw.strip()

    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Иногда модель добавляет пояснение до или после объекта.
        braces = re.search(r"\{.*\}", text, re.DOTALL)
        if braces is None:
            raise
        parsed = json.loads(braces.group(0))

    if not isinstance(parsed, dict):
        raise ValueError(f"Ожидался JSON-объект, получен {type(parsed).__name__}")
    return parsed


def _phrase_in_listing(phrase: str, apartment: dict[str, Any]) -> bool:
    """Проверяет, что цитата действительно встречается в объявлении.

    Сравнение идёт по буквам и цифрам: модель нередко меняет пунктуацию,
    переносы и регистр, но переписывает слова точно.
    """
    if not phrase:
        return False

    def squeeze(text: str) -> str:
        return _NON_WORD.sub(" ", text.lower()).strip()

    haystack = squeeze(
        f"{apartment.get('title') or ''} {apartment.get('description') or ''}"
    )
    return squeeze(phrase) in haystack


def listing_type_reason(apartment: dict[str, Any]) -> str | None:
    """Почему это не обычная долгосрочная аренда квартиры, или None."""
    title = str(apartment.get("title") or "")
    description = str(apartment.get("description") or "")
    text = f"{title} {description}"
    if _WANTED_TITLE.search(title):
        return "ищут квартиру, а не сдают"
    if _SWAP_LISTING.search(text):
        return "обмен (Tauschwohnung), не аренда"
    if _HOLIDAY_LISTING.search(text):
        return "Ferienwohnung / краткосрочная аренда"
    if _SHORT_STAY.search(text):
        return "Zwischenmiete / краткосрочный съём"
    if _NOT_OFFERING.search(text):
        return "ищут квартиру, а не сдают"
    return None


def shared_wg_reason(profile: dict[str, Any], apartment: dict[str, Any]) -> str | None:
    """Почему это съём WG / цена за человека, или None, если обычная квартира."""
    text = f"{apartment.get('title') or ''} {apartment.get('description') or ''}"
    if _WG_ROOM.search(text):
        return "комната в WG"
    if not _WG_FLAT.search(text):
        return None
    if str(profile.get("household_type") or "") == "wg":
        return None
    return "сдаётся как WG / цена за человека"


_FEMALE_ONLY = re.compile(
    r"(?:nur|ausschlie(?:ss|ß)lich)\s+(?:für\s+|an\s+)?(?:frauen|damen|studentinnen|weiblich|eine\s+frau)"
    r"|frauen[\s\-]?wg|damen[\s\-]?wg|(?:keine|nicht\s+für)\s+männer"
    r"|\bnachmieterin\b|\bmitbewohnerin\s+gesucht\b"
    r"|(?:weibliche[rn]?|nur\s+eine)\s+(?:nach|unter)?mieterin"
    r"|\bmieterin\s+gesucht\b"
    r"|bevorzugt(?:e)?\s+(?:frauen|damen|weiblich)",
    re.IGNORECASE,
)
_TITLE_FEMALE_ONLY = re.compile(
    r"\bnachmieterin\b|\bmieterin\s+gesucht\b|"
    r"\bweibliche[rn]?\s+(?:nach|unter)?mieterin\b|"
    r"frauen[\s\-]?wg|damen[\s\-]?wg|"
    r"nur\s+für\s+(?:frauen|damen|weiblich|eine\s+frau)",
    re.IGNORECASE,
)
_MALE_ONLY = re.compile(
    r"(?:nur|ausschlie(?:ss|ß)lich)\s+(?:für\s+)?(?:männer|herren|männlich)"
    r"|männer[\s\-]?wg|herren[\s\-]?wg|(?:keine|nicht\s+für)\s+frauen",
    re.IGNORECASE,
)
_GENDER_MIXED = re.compile(
    r"frauen\s+und\s+männer|männer\s+und\s+frauen|"
    r"studentinnen\s+und\s+studenten|studenten\s+und\s+studentinnen",
    re.IGNORECASE,
)


def gender_restriction_reason(
    profile: dict[str, Any], apartment: dict[str, Any]
) -> str | None:
    """Почему объявление закрыто по полу заявителя, или None."""
    gender = str(profile.get("applicant_gender") or "").strip().lower()
    if gender not in {"male", "female"}:
        return None
    title = str(apartment.get("title") or "")
    description = str(apartment.get("description") or "")
    text = f"{title} {description}"
    if gender == "male" and _TITLE_FEMALE_ONLY.search(title):
        return "объявление только для женщин"
    if _GENDER_MIXED.search(text):
        if gender == "male" and re.search(r"frauen[\s\-]?wg", text, re.IGNORECASE):
            return "объявление только для женщин"
        if gender == "female" and re.search(r"männer[\s\-]?wg", text, re.IGNORECASE):
            return "объявление только для мужчин"
        return None
    if gender == "male" and _FEMALE_ONLY.search(text):
        return "объявление только для женщин"
    if gender == "female" and _MALE_ONLY.search(text):
        return "объявление только для мужчин"
    return None


def _occupancy_limit(phrase: str) -> int | None:
    """Сколько человек объявление явно готово принять, если это сказано.

    Нужно, чтобы не принять отказ «max. 2 Personen» против двоих в анкете:
    модель путает «лимит равен размеру хозяйства» с «лимит меньше».
    """
    text = phrase.casefold()
    if re.search(r"paar|pärchen", text):
        # «Paare oder kleine Familien» — для двоих точно подходит, лимит
        # семёрки отсюда не вывести.
        if "familie" in text:
            return None
        return 2
    if re.search(r"\b(1|eine)\s+person", text) or "einzelperson" in text:
        if "oder" not in text and "paar" not in text:
            return 1
    match = re.search(r"(?:max(?:imal|\.)?|höchstens|bis\s+zu)\s*(\d+)", text)
    if match:
        return int(match.group(1))
    match = re.search(r"(?:für|geeignet für)\s*(\d+)\s*personen", text)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)\s*personen", text)
    if match:
        return int(match.group(1))
    return None


def _reject_is_invalid(
    parsed: dict[str, Any], profile: dict[str, Any], apartment: dict[str, Any]
) -> str | None:
    """Проверяет отказ модели. Возвращает причину отмены или None, если отказ верен."""
    fact = str(parsed.get("blocking_fact") or "").strip().lower()
    phrase = str(parsed.get("blocking_phrase") or "").strip()

    if fact not in _BLOCKING_FACTS:
        # Модель придумала категорию: семейное положение, возраст, профессия.
        # Анкета об этом не спрашивает, значит и противоречия быть не может.
        return f"причина {fact!r} вне списка"

    if fact in _PHRASE_FACTS and not _phrase_in_listing(phrase, apartment):
        return f"цитата {phrase[:60]!r} не найдена в объявлении"

    # Категорию модель подбирает под уже принятое решение, поэтому сверяем её
    # с анкетой: запрет животных не мешает тому, у кого их нет.
    if fact == "pets" and not profile.get("has_pets"):
        return "у арендатора нет животных"

    if fact == "notes" and not str(profile.get("custom_notes") or "").strip():
        return "в анкете нет свободных пожеланий"

    if fact == "not_a_rental":
        if listing_type_reason(apartment) is None and shared_wg_reason(
            profile, apartment
        ) is None:
            return "это обычная долгосрочная аренда"
        return None

    if fact == "city":
        if city_mismatch_reason(profile, apartment) is None:
            return "город в радиусе поиска"
        return None

    if fact == "household":
        # На фоне WG «max. 2 Personen» — это лимит комнаты, а не квартиры.
        # Отказ модели оставляем: отменять его нельзя.
        if shared_wg_reason(profile, apartment):
            return None
        household = profile.get("household_size")
        if not household:
            return "в анкете не указано число жильцов"
        phrase_l = phrase.casefold()
        if str(profile.get("household_type") or "") == "family" and re.search(
            r"keine\s+familien", phrase_l
        ):
            return None
        limit = _occupancy_limit(phrase)
        if limit is not None:
            if household <= limit:
                return f"въезжает {household}, объявление берёт до {limit}"
            return None
        if household <= 2:
            # «keine Familien», «nur Paare» без числа: двое проходят,
            # кроме семьи с детьми — её уже отсекли выше.
            return "двое проходят как Paare / max. 2"

    if fact == "gender":
        if not profile.get("applicant_gender"):
            return "пол не указан"
        if gender_restriction_reason(profile, apartment) is None:
            return "объявление не ограничивает этот пол"
        return None

    if fact == "area":
        if profile.get("sqm_min") is None and not profile.get("sqm_max"):
            return "в анкете не указана площадь"
        area = apartment.get("sqm")
        if area is not None:
            too_small = area_is_too_small(profile.get("sqm_min"), area)
            maximum = profile.get("sqm_max")
            too_big = (
                maximum is not None
                and float(maximum) > 0
                and float(area) > float(maximum)
            )
            if not too_small and not too_big:
                return "площадь в допуске к запрошенной"

    if fact == "wbs":
        # «Nur mit WBS» режет только тех, у кого сертификата нет. Наличие WBS
        # само по себе никогда не причина отказа.
        if profile.get("has_wbs"):
            return "у арендатора есть WBS"

    if fact == "jobcenter":
        # «Kein Jobcenter» / «nur Berufstätige» режет только тех, кто платит
        # через соцпомощь. Остальным эта фраза не мешает.
        if not profile.get("uses_jobcenter"):
            return "арендатор не на Jobcenter"

    return None


async def _ask(
    system_prompt: str, payload: str, max_tokens: int, listing_id: Any
) -> dict[str, Any]:
    """Один запрос к модели с ответом в JSON. Бросает RuntimeError при сбое."""
    try:
        response = await _client().chat.completions.create(
            model=MODEL,
            response_format={"type": "json_object"},
            temperature=0.4,
            max_completion_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": payload},
            ],
        )
    except APIError as error:
        logger.exception("OpenAI отклонил запрос по объявлению %s", listing_id)
        raise RuntimeError(f"OpenAI API: {error.__class__.__name__}") from error
    except Exception as error:
        logger.exception("Сбой обращения к OpenAI по объявлению %s", listing_id)
        raise RuntimeError(f"{error.__class__.__name__}: {error}") from error

    if response.choices and response.choices[0].finish_reason == "length":
        # Ответ упёрся в потолок токенов — JSON почти наверняка оборван.
        logger.warning(
            "Ответ по объявлению %s обрезан лимитом в %d токенов", listing_id, max_tokens
        )

    raw = (response.choices[0].message.content or "").strip() if response.choices else ""
    if not raw:
        raise RuntimeError("OpenAI вернул пустой ответ")

    try:
        return _extract_json(raw)
    except (json.JSONDecodeError, ValueError) as error:
        logger.error(
            "Не удалось разобрать JSON от OpenAI по объявлению %s. Ответ: %.500s",
            listing_id,
            raw,
        )
        raise RuntimeError("Модель вернула ответ не в формате JSON") from error


def _failure(reason: str) -> dict[str, Any]:
    """Ответ на случай, когда модель недоступна или ответила мусором."""
    return {"match": False, "reason": reason, "anschreiben": "", "error": True}


async def analyze_apartment_and_generate_letter(
    user_profile: dict[str, Any],
    apartment: dict[str, Any],
) -> dict[str, Any]:
    """Оценивает объявление и, если оно подходит, пишет Anschreiben.

    Всегда возвращает словарь с ключами match, reason и anschreiben. При сбое
    обращения к OpenAI добавляется ключ error=True, чтобы вызывающий код мог
    отличить «не подошло» от «оценить не удалось».
    """
    language = _LANGUAGE_NAMES.get(
        str(user_profile.get("language") or DEFAULT_LANG), _LANGUAGE_NAMES["ua"]
    )
    listing_id = apartment.get("external_id")
    payload = _build_payload(user_profile, apartment)

    try:
        decision = await _ask(
            _DECISION_PROMPT.format(language=language),
            payload,
            MAX_DECISION_TOKENS,
            listing_id,
        )
    except RuntimeError as error:
        return _failure(str(error))

    match = bool(decision.get("match"))
    reason = str(decision.get("reason") or "").strip()

    if not match:
        overturned = _reject_is_invalid(decision, user_profile, apartment)
        if overturned is not None:
            logger.info("Отказ по объявлению %s отменён: %s", listing_id, overturned)
            match = True
            # Объяснение отказа в карточке подошедшей квартиры только запутает.
            reason = ""

    # Страховка: модель может пропустить «pro Person» / «WG Vermietung»,
    # а отмена отказа по «max. 2 Personen» как раз так и показала эту квартиру.
    wg_reason = shared_wg_reason(user_profile, apartment)
    if match and wg_reason:
        logger.info("Объявление %s отклонено кодом: %s", listing_id, wg_reason)
        return {"match": False, "reason": wg_reason, "anschreiben": ""}

    kind_reason = listing_type_reason(apartment)
    if match and kind_reason:
        logger.info("Объявление %s отклонено кодом: %s", listing_id, kind_reason)
        return {"match": False, "reason": kind_reason, "anschreiben": ""}

    gender_reason = gender_restriction_reason(user_profile, apartment)
    if match and gender_reason:
        logger.info("Объявление %s отклонено кодом: %s", listing_id, gender_reason)
        return {"match": False, "reason": gender_reason, "anschreiben": ""}

    sqm_min = user_profile.get("sqm_min")
    area = apartment.get("sqm")
    if match and area_below_minimum(sqm_min, area):
        area_reason = f"площадь {area} м² < минимум {sqm_min} м²"
        logger.info("Объявление %s отклонено кодом: %s", listing_id, area_reason)
        return {"match": False, "reason": area_reason, "anschreiben": ""}

    city_reason = city_mismatch_reason(user_profile, apartment)
    if match and city_reason:
        logger.info("Объявление %s отклонено кодом: %s", listing_id, city_reason)
        return {"match": False, "reason": city_reason, "anschreiben": ""}

    budget = user_profile.get("budget_max")
    price = apartment.get("price")
    if match and budget is not None and price is not None and price > budget:
        over = f"цена {int(price)} € > бюджет {int(budget)} €"
        logger.info("Объявление %s отклонено кодом: %s", listing_id, over)
        return {"match": False, "reason": over, "anschreiben": ""}

    kalt_reason = kalt_only_budget_reason(
        budget, price, apartment.get("price_kind")
    )
    if match and kalt_reason:
        logger.info("Объявление %s отклонено кодом: %s", listing_id, kalt_reason)
        return {"match": False, "reason": kalt_reason, "anschreiben": ""}

    if not match:
        logger.info("Объявление %s отклонено: %s", listing_id, reason)
        return {"match": False, "reason": reason, "anschreiben": ""}

    try:
        letter = str((await _ask(
            _LETTER_PROMPT, payload, MAX_LETTER_TOKENS, listing_id
        )).get("anschreiben") or "").strip()
    except RuntimeError as error:
        # Объявление подходит — показываем его даже без письма: карточка со
        # ссылкой полезнее, чем сообщение об ошибке.
        logger.warning("Письмо по объявлению %s не написано: %s", listing_id, error)
        letter = ""

    letter = apply_letter_income_policy(letter, user_profile, apartment)
    letter = apply_letter_salutation(letter, salutation_from_listing(apartment))
    letter = apply_letter_signature(letter, user_profile)
    logger.info("Объявление %s подошло, длина письма %d", listing_id, len(letter))
    return {"match": True, "reason": reason, "anschreiben": letter}
