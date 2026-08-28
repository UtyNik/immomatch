"""Пошаговая анкета пользователя (FSM-онбординг)."""

from __future__ import annotations

import logging
from typing import Any, Final

from aiogram import Bot, F, Router, html
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, User
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import (
    clear_seen_apartments,
    get_user,
    save_user_profile,
    toggle_auto_search,
    update_user_language,
)
from handlers.common import Sender, drop_keyboard, sender
from keyboards import CB_AUTO_SEARCH, CB_SEARCH, PROFILE_BUTTON_TEXTS, profile_reply_keyboard
from services.translator import normalize_and_translate_user_input
from states import OnboardingStates
from texts import CHOOSE_LANGUAGE, DEFAULT_LANG, LANGUAGES, WELCOME_TEXT, t
from validators import (
    BUDGET_MAX,
    BUDGET_MIN,
    CITY_MAX_LEN,
    CITY_MIN_LEN,
    HOUSEHOLD_MAX,
    HOUSEHOLD_MIN,
    INCOME_MAX,
    INCOME_MIN,
    NAME_MAX_LEN,
    NAME_MIN_LEN,
    NOTES_MAX_LEN,
    ROOMS_MAX,
    ROOMS_MIN,
    SEARCH_RADII,
    SQM_MAX,
    SQM_MIN,
    is_valid_city,
    is_valid_name,
    parse_amount,
    parse_applicant_gender,
    parse_count,
    parse_household_type,
    parse_number,
    parse_search_radius,
    parse_sqm,
)

logger = logging.getLogger(__name__)

router = Router(name="onboarding")

# Выбор языка на первом шаге анкеты и смена языка у готового профиля — разные
# сценарии, поэтому и префиксы разные: первый ведёт к вопросам, второй только
# меняет язык и не трогает сохранённые ответы.
CB_LANG_PREFIX: Final[str] = "lang:"
CB_SETLANG_PREFIX: Final[str] = "setlang:"
CB_YES: Final[str] = "answer:yes"
CB_NO: Final[str] = "answer:no"
CB_SKIP: Final[str] = "notes:skip"
CB_SKIP_INCOME: Final[str] = "skip_income"
CB_SQM_UNLIMITED: Final[str] = "sqm:unlimited"
CB_LANG_MENU: Final[str] = "profile:lang"
CB_NEW_PROFILE: Final[str] = "profile:new"
CB_EDIT_MENU: Final[str] = "profile:edit"
CB_EDIT_PREFIX: Final[str] = "edit:"
CB_EDIT_BACK: Final[str] = "edit:back"
CB_RADIUS_PREFIX: Final[str] = "radius:"
CB_GENDER_MALE: Final[str] = "gender_male"
CB_GENDER_FEMALE: Final[str] = "gender_female"
# callback_data кнопки → значение household_type в БД.
HTYPE_CALLBACKS: Final[dict[str, str]] = {
    "htype_partner_female": "partner_female",
    "htype_partner_male": "partner_male",
    "htype_family": "family",
    "htype_wg": "wg",
}
EDITABLE_FIELDS: Final[tuple[str, ...]] = (
    "name",
    "gender",
    "city",
    "radius",
    "budget",
    "rooms",
    "sqm",
    "household",
    "wbs",
    "jobcenter",
    "pets",
    "income",
    "notes",
)
# Смена этих полей меняет выдачу Kleinanzeigen / отсев — нужен новый
# глубокий обход (до 3 страниц) и сброс seen.
_SEARCH_RESET_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "city",
        "city_de",
        "search_radius",
        "budget_max",
        "rooms_min",
        "sqm_min",
        "sqm_max",
        "household_size",
        "household_type",
        "has_pets",
    }
)


# --------------------------------------------------------------------------- #
# Клавиатуры
# --------------------------------------------------------------------------- #
def language_keyboard(prefix: str) -> InlineKeyboardMarkup:
    """Кнопки выбора языка — по одной в строке.

    Префикс задаёт сценарий: шаг анкеты или смена языка у готового профиля.
    """
    builder = InlineKeyboardBuilder()
    for code, title in LANGUAGES.items():
        builder.button(text=title, callback_data=f"{prefix}{code}")
    builder.adjust(1)
    return builder.as_markup()


def yes_no_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Кнопки «Да»/«Нет» на языке пользователя."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t(lang, "btn_yes"), callback_data=CB_YES)
    builder.button(text=t(lang, "btn_no"), callback_data=CB_NO)
    builder.adjust(2)
    return builder.as_markup()


def skip_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Кнопка пропуска свободных пожеланий."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t(lang, "btn_skip"), callback_data=CB_SKIP)
    return builder.as_markup()


def skip_income_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Кнопка пропуска чистого дохода: поле необязательное."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t(lang, "btn_skip_income"), callback_data=CB_SKIP_INCOME)
    return builder.as_markup()


def unlimited_sqm_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Кнопка «без верхней границы» на шаге максимальной площади."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t(lang, "btn_sqm_unlimited"), callback_data=CB_SQM_UNLIMITED)
    return builder.as_markup()


def radius_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Кнопки Umkreis: только город или +5/+10/+20/+50 км."""
    builder = InlineKeyboardBuilder()
    for km in SEARCH_RADII:
        builder.button(text=t(lang, f"btn_radius_{km}"), callback_data=f"{CB_RADIUS_PREFIX}{km}")
    builder.adjust(1)
    return builder.as_markup()


def gender_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Пол заявителя: нужен для Anschreiben и отсева «nur für Frauen»."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t(lang, "btn_gender_male"), callback_data=CB_GENDER_MALE)
    builder.button(text=t(lang, "btn_gender_female"), callback_data=CB_GENDER_FEMALE)
    builder.adjust(2)
    return builder.as_markup()


def household_type_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Состав жильцов, если въезжает больше одного человека."""
    builder = InlineKeyboardBuilder()
    for callback, kind in HTYPE_CALLBACKS.items():
        builder.button(text=t(lang, f"btn_htype_{kind}"), callback_data=callback)
    builder.adjust(1)
    return builder.as_markup()


def edit_field_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Список полей анкеты, которые можно поменять по одному."""
    builder = InlineKeyboardBuilder()
    for field in EDITABLE_FIELDS:
        builder.button(
            text=t(lang, f"btn_edit_{field}"),
            callback_data=f"{CB_EDIT_PREFIX}{field}",
        )
    builder.button(text=t(lang, "btn_edit_back"), callback_data=CB_EDIT_BACK)
    builder.adjust(2)
    return builder.as_markup()


def profile_keyboard(
    lang: str, profile: dict[str, Any] | None = None
) -> InlineKeyboardMarkup:
    """Кнопки под карточкой: поиск, автопоиск, язык, правка поля, новая анкета."""
    enabled = True
    if profile is not None and profile.get("is_auto_search_enabled") is not None:
        enabled = bool(profile["is_auto_search_enabled"])
    auto_key = "btn_auto_search_on" if enabled else "btn_auto_search_off"

    builder = InlineKeyboardBuilder()
    builder.button(text=t(lang, "btn_search"), callback_data=CB_SEARCH)
    builder.button(text=t(lang, auto_key), callback_data=CB_AUTO_SEARCH)
    builder.button(text=t(lang, "btn_change_lang"), callback_data=CB_LANG_MENU)
    builder.button(text=t(lang, "btn_edit_profile"), callback_data=CB_EDIT_MENU)
    builder.button(text=t(lang, "btn_new_profile"), callback_data=CB_NEW_PROFILE)
    builder.adjust(1)
    return builder.as_markup()


# --------------------------------------------------------------------------- #
# Вспомогательные функции
# --------------------------------------------------------------------------- #
async def _lang_of(state: FSMContext) -> str:
    """Язык, выбранный на первом шаге; до выбора — язык по умолчанию."""
    data = await state.get_data()
    return str(data.get("language") or DEFAULT_LANG)


def _format_rooms(value: float | None) -> str | None:
    """Печатает 3.0 как «3», а 2.5 оставляет как есть."""
    if value is None:
        return None
    return str(int(value)) if float(value).is_integer() else str(value)


def _format_area_range(lang: str, profile: dict[str, Any], empty: str) -> str:
    """«от 40 м² до 80 м²» или «от 40 м²», если максимума нет."""
    minimum = profile.get("sqm_min")
    if minimum is None:
        return empty
    shown_min = _format_rooms(float(minimum))
    maximum = profile.get("sqm_max")
    if maximum is None:
        return t(lang, "sqm_from", min=shown_min)
    return t(lang, "sqm_range", min=shown_min, max=_format_rooms(float(maximum)))


def _format_income(lang: str, value: Any) -> str:
    """Сумма Netto или пометка, что поле пропущено."""
    if value is None or value == "":
        return t(lang, "f_income_skipped")
    return f"{int(value)} €"


def render_profile(lang: str, profile: dict[str, Any]) -> str:
    """Собирает итоговую карточку анкеты в HTML."""
    empty = t(lang, "empty")

    def flag(value: Any) -> str:
        if value is None:
            return empty
        return t(lang, "yes") if value else t(lang, "no")

    def money(value: Any) -> str:
        return f"{int(value)} €" if value is not None else empty

    city = profile.get("city_de") or profile.get("city")
    notes = profile.get("custom_notes")
    rooms = _format_rooms(profile.get("rooms_min"))
    household = profile.get("household_size")
    first = str(profile.get("first_name") or "").strip()
    last = str(profile.get("last_name") or "").strip()
    full_name = " ".join(part for part in (first, last) if part)
    radius = parse_search_radius(profile.get("search_radius"))
    if city and radius > 0:
        city_shown = t(lang, "city_radius", city=html.quote(str(city)), km=radius)
    elif city:
        city_shown = html.quote(str(city))
    else:
        city_shown = empty

    gender = parse_applicant_gender(profile.get("applicant_gender"))
    gender_icon = "👨" if gender == "male" else "👩" if gender == "female" else "👤"
    gender_label = t(lang, f"gender_{gender}") if gender else empty
    household_label = empty
    if household is not None:
        kind = parse_household_type(profile.get("household_type"))
        if kind:
            household_label = t(
                lang,
                "household_with_type",
                count=household,
                kind=t(lang, f"htype_{kind}"),
            )
        else:
            household_label = str(household)

    lines = [
        t(lang, "card_title"),
        "",
        f"👤 <b>{t(lang, 'f_name')}:</b> {html.quote(full_name) if full_name else empty}",
        f"{gender_icon} <b>{t(lang, 'f_applicant')}:</b> {gender_label} | "
        f"👥 <b>{t(lang, 'f_household')}:</b> {household_label}",
        f"🏙 <b>{t(lang, 'f_city')}:</b> {city_shown}",
        f"💶 <b>{t(lang, 'f_budget')}:</b> {money(profile.get('budget_max'))}",
        f"🚪 <b>{t(lang, 'f_rooms')}:</b> {rooms or empty}",
        f"📐 <b>{t(lang, 'f_sqm')}:</b> {_format_area_range(lang, profile, empty)}",
        f"📄 <b>{t(lang, 'f_wbs')}:</b> {flag(profile.get('has_wbs'))}",
        f"🏛 <b>{t(lang, 'f_jobcenter')}:</b> {flag(profile.get('uses_jobcenter'))}",
        f"🐾 <b>{t(lang, 'f_pets')}:</b> {flag(profile.get('has_pets'))}",
        f"💰 <b>{t(lang, 'f_income')}:</b> {_format_income(lang, profile.get('net_income'))}",
        f"📝 <b>{t(lang, 'f_notes')}:</b> "
        + (f"<i>{html.quote(notes)}</i>" if notes else empty),
    ]
    return "\n".join(lines)


async def _start_survey(send: Sender, state: FSMContext, lang: str | None = None) -> None:
    """Начинает анкету заново.

    Если язык уже известен, вопрос о языке пропускается: менять язык нужно
    отдельной кнопкой, а не ценой повторного заполнения анкеты.
    """
    await state.clear()

    if lang is None:
        await state.set_state(OnboardingStates.language)
        await send(CHOOSE_LANGUAGE, reply_markup=language_keyboard(CB_LANG_PREFIX))
        return

    await state.update_data(language=lang)
    await state.set_state(OnboardingStates.gender)
    await send(t(lang, "ask_gender"), reply_markup=gender_keyboard(lang))


async def prompt_missing_gender(
    send: Sender, state: FSMContext, lang: str, *, announce: bool = True
) -> None:
    """Спрашивает пол у тех, кто заполнил анкету раньше."""
    await state.set_state(OnboardingStates.gender)
    await state.update_data(language=lang, gender_only=True)
    if announce:
        await send(t(lang, "household_missing"))
    await send(t(lang, "ask_gender"), reply_markup=gender_keyboard(lang))


async def prompt_missing_names(
    send: Sender, state: FSMContext, lang: str, *, announce: bool = True
) -> None:
    """Спрашивает имя и фамилию у тех, кто заполнил анкету раньше."""
    await state.set_state(OnboardingStates.first_name)
    await state.update_data(language=lang, names_only=True)
    if announce:
        await send(t(lang, "household_missing"))
    await send(t(lang, "ask_first_name"))


async def prompt_missing_household(
    send: Sender, state: FSMContext, lang: str, *, announce: bool = True
) -> None:
    """Спрашивает только число жильцов у тех, кто заполнил анкету раньше."""
    await state.set_state(OnboardingStates.household)
    await state.update_data(language=lang, household_only=True)
    if announce:
        await send(t(lang, "household_missing"))
    await send(t(lang, "ask_household"))


async def prompt_missing_household_type(
    send: Sender, state: FSMContext, lang: str, *, announce: bool = True
) -> None:
    """Спрашивает состав, если число жильцов уже есть, а тип — нет."""
    await state.set_state(OnboardingStates.household_type)
    await state.update_data(language=lang, household_type_only=True)
    if announce:
        await send(t(lang, "household_missing"))
    await send(t(lang, "ask_household_type"), reply_markup=household_type_keyboard(lang))


async def prompt_missing_sqm(
    send: Sender, state: FSMContext, lang: str, *, announce: bool = True
) -> None:
    """Спрашивает диапазон площади у тех, кто заполнил анкету раньше."""
    await state.set_state(OnboardingStates.sqm_min)
    await state.update_data(language=lang, sqm_only=True)
    if announce:
        await send(t(lang, "household_missing"))
    await send(t(lang, "ask_sqm_min"))


async def prompt_missing_support(
    send: Sender, state: FSMContext, lang: str, *, announce: bool = True
) -> None:
    """Спрашивает WBS и Jobcenter отдельно: раньше это был один шаг."""
    await state.set_state(OnboardingStates.wbs)
    await state.update_data(language=lang, support_only=True)
    if announce:
        await send(t(lang, "household_missing"))
    await send(t(lang, "ask_wbs"), reply_markup=yes_no_keyboard(lang))


async def prompt_missing_profile_fields(
    send: Sender, state: FSMContext, lang: str, profile: dict[str, Any],
    *, announce: bool = True,
) -> bool:
    """Спрашивает поля, которых не было в старых анкетах. True, если спросил."""
    if parse_applicant_gender(profile.get("applicant_gender")) is None:
        await prompt_missing_gender(send, state, lang, announce=announce)
        return True
    if not str(profile.get("first_name") or "").strip() or not str(
        profile.get("last_name") or ""
    ).strip():
        await prompt_missing_names(send, state, lang, announce=announce)
        return True
    if profile.get("sqm_min") is None:
        await prompt_missing_sqm(send, state, lang, announce=announce)
        return True
    if profile.get("household_size") is None:
        await prompt_missing_household(send, state, lang, announce=announce)
        return True
    size = profile.get("household_size")
    if size == 1 and parse_household_type(profile.get("household_type")) is None:
        profile["household_type"] = "single"
        try:
            await save_user_profile(profile)
        except Exception:
            logger.exception("Не удалось записать household_type=single для %s", profile.get("user_id"))
    elif (
        size is not None
        and int(size) > 1
        and parse_household_type(profile.get("household_type")) is None
    ):
        await prompt_missing_household_type(send, state, lang, announce=announce)
        return True
    if profile.get("has_wbs") is None or profile.get("uses_jobcenter") is None:
        await prompt_missing_support(send, state, lang, announce=announce)
        return True
    return False


async def _after_partial_save(
    send: Sender, state: FSMContext, lang: str, profile: dict[str, Any]
) -> None:
    """Либо следующий недостающий вопрос, либо готовая карточка."""
    await state.clear()
    if await prompt_missing_profile_fields(
        send, state, lang, profile, announce=False
    ):
        return
    await send(t(lang, "saved"), reply_markup=profile_reply_keyboard(lang))
    await send(render_profile(lang, profile), reply_markup=profile_keyboard(lang, profile))


def _normalize_search_field(key: str, value: Any) -> Any:
    """Приводит поле фильтра к виду, в котором можно сравнить «было / стало»."""
    if key in {"city", "city_de", "household_type"}:
        text = str(value or "").strip().casefold()
        return text or None
    if key == "search_radius":
        return parse_search_radius(value)
    if key == "has_pets":
        return None if value is None else bool(value)
    if key in {"budget_max", "household_size"}:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if key in {"rooms_min", "sqm_min", "sqm_max"}:
        if value is None or value == "":
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return value
        if key == "sqm_max" and number == 0:
            return None
        return number
    return value


def _search_filters_changed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """True, если поменялись условия, от которых зависит набор объявлений."""
    return any(
        _normalize_search_field(key, before.get(key))
        != _normalize_search_field(key, after.get(key))
        for key in _SEARCH_RESET_FIELDS
    )


async def _patch_profile(
    user: User,
    lang: str,
    state: FSMContext,
    send: Sender,
    updates: dict[str, Any],
    *,
    reset_seen: bool = False,
) -> None:
    """Пишет в готовую анкету только переданные поля и показывает карточку."""
    profile = await get_user(user.id)
    if profile is None:
        await state.clear()
        await send(t(lang, "no_profile"))
        return

    before = dict(profile)
    profile["username"] = user.username
    profile.update(updates)
    try:
        await save_user_profile(profile)
        if reset_seen or _search_filters_changed(before, profile):
            await clear_seen_apartments(user.id)
            logger.info(
                "Пользователь %s: сброс seen — изменились условия поиска",
                user.id,
            )
    except Exception:
        logger.exception("Не удалось обновить анкету пользователя %s", user.id)
        await send(t(lang, "save_failed"))
        return

    await _after_partial_save(send, state, lang, profile)


async def _save_household(
    user: User,
    people: int,
    lang: str,
    state: FSMContext,
    send: Sender,
    *,
    household_type: str | None = None,
) -> None:
    """Дописывает число жильцов и состав в готовую анкету."""
    profile = await get_user(user.id)
    if profile is None:
        await state.clear()
        await send(t(lang, "no_profile"))
        return

    before = dict(profile)
    profile["username"] = user.username
    profile["household_size"] = people
    if household_type:
        profile["household_type"] = household_type
    elif people == 1:
        profile["household_type"] = "single"

    try:
        await save_user_profile(profile)
        if _search_filters_changed(before, profile):
            await clear_seen_apartments(user.id)
            logger.info(
                "Пользователь %s: сброс seen — изменились условия поиска",
                user.id,
            )
    except Exception:
        logger.exception("Не удалось дополнить анкету пользователя %s", user.id)
        await send(t(lang, "save_failed"))
        return

    await _after_partial_save(send, state, lang, profile)


async def _save_gender(
    user: User, gender: str, lang: str, state: FSMContext, send: Sender
) -> None:
    """Дописывает пол в готовую анкету."""
    profile = await get_user(user.id)
    if profile is None:
        await state.clear()
        await send(t(lang, "no_profile"))
        return

    profile["username"] = user.username
    profile["applicant_gender"] = gender

    try:
        await save_user_profile(profile)
    except Exception:
        logger.exception("Не удалось дополнить анкету пользователя %s", user.id)
        await send(t(lang, "save_failed"))
        return

    await _after_partial_save(send, state, lang, profile)


async def _save_sqm(
    user: User, lang: str, state: FSMContext, send: Sender
) -> None:
    """Дописывает диапазон площади в готовую анкету."""
    data = await state.get_data()
    profile = await get_user(user.id)
    if profile is None:
        await state.clear()
        await send(t(lang, "no_profile"))
        return

    before = dict(profile)
    profile["username"] = user.username
    profile["sqm_min"] = data.get("sqm_min")
    profile["sqm_max"] = data.get("sqm_max")

    try:
        await save_user_profile(profile)
        if _search_filters_changed(before, profile):
            await clear_seen_apartments(user.id)
            logger.info(
                "Пользователь %s: сброс seen — изменились условия поиска",
                user.id,
            )
    except Exception:
        logger.exception("Не удалось дополнить анкету пользователя %s", user.id)
        await send(t(lang, "save_failed"))
        return

    await _after_partial_save(send, state, lang, profile)


async def _save_support_flags(
    user: User, lang: str, state: FSMContext, send: Sender
) -> None:
    """Дописывает WBS и Jobcenter в готовую анкету."""
    data = await state.get_data()
    profile = await get_user(user.id)
    if profile is None:
        await state.clear()
        await send(t(lang, "no_profile"))
        return

    profile["username"] = user.username
    profile["has_wbs"] = data.get("has_wbs")
    profile["uses_jobcenter"] = data.get("uses_jobcenter")

    try:
        await save_user_profile(profile)
    except Exception:
        logger.exception("Не удалось дополнить анкету пользователя %s", user.id)
        await send(t(lang, "save_failed"))
        return

    await _after_partial_save(send, state, lang, profile)


async def _normalize_identity(first_name: str, last_name: str, city: str) -> dict[str, str]:
    """Латиница и немецкий город. Ошибка модели не должна ронять сохранение анкеты."""
    try:
        return await normalize_and_translate_user_input(first_name, last_name, city)
    except Exception:
        logger.exception("Нормализация имени/города не удалась")
        return {
            "first_name_latin": first_name.strip(),
            "last_name_latin": last_name.strip(),
            "city_de": city.strip(),
        }


async def _save_names(
    user: User, lang: str, state: FSMContext, send: Sender
) -> None:
    """Дописывает имя, фамилию и city_de в готовую анкету."""
    data = await state.get_data()
    profile = await get_user(user.id)
    if profile is None:
        await state.clear()
        await send(t(lang, "no_profile"))
        return

    raw_first = str(data.get("first_name") or "").strip()
    raw_last = str(data.get("last_name") or "").strip()
    city = str(profile.get("city") or "")
    normalized = await _normalize_identity(raw_first, raw_last, city)

    profile["username"] = user.username
    profile["first_name"] = normalized["first_name_latin"]
    profile["last_name"] = normalized["last_name_latin"]
    if normalized["city_de"]:
        profile["city_de"] = normalized["city_de"]

    try:
        await save_user_profile(profile)
    except Exception:
        logger.exception("Не удалось дополнить анкету пользователя %s", user.id)
        await send(t(lang, "save_failed"))
        return

    await _after_partial_save(send, state, lang, profile)


async def _finish_onboarding(state: FSMContext, user: User, send: Sender) -> None:
    """Сохраняет анкету в БД и показывает итоговую карточку."""
    data = await state.get_data()
    lang = str(data.get("language") or DEFAULT_LANG)
    raw_first = str(data.get("first_name") or "").strip()
    raw_last = str(data.get("last_name") or "").strip()
    city = str(data.get("city") or "")
    normalized = await _normalize_identity(raw_first, raw_last, city)

    profile: dict[str, Any] = {
        "user_id": user.id,
        "username": user.username,
        "language": lang,
        "first_name": normalized["first_name_latin"],
        "last_name": normalized["last_name_latin"],
        "city": city,
        "city_de": normalized["city_de"] or city,
        "search_radius": parse_search_radius(data.get("search_radius")),
        "applicant_gender": parse_applicant_gender(data.get("applicant_gender")),
        "budget_max": data.get("budget_max"),
        "rooms_min": data.get("rooms_min"),
        "sqm_min": data.get("sqm_min"),
        "sqm_max": data.get("sqm_max"),
        "household_size": data.get("household_size"),
        "household_type": parse_household_type(data.get("household_type")),
        "has_wbs": data.get("has_wbs"),
        "uses_jobcenter": data.get("uses_jobcenter"),
        "has_pets": data.get("has_pets"),
        "net_income": data.get("net_income"),
        "custom_notes": data.get("custom_notes"),
        "is_active": True,
    }

    try:
        await save_user_profile(profile)
        # Новая анкета — снова глубокий обход выдачи, старые seen не тащим.
        await clear_seen_apartments(user.id)
    except Exception:
        # Данные анкеты остаются в FSM, поэтому пользователь может повторить шаг.
        logger.exception("Не удалось сохранить анкету пользователя %s", user.id)
        await send(t(lang, "save_failed"))
        return

    await state.clear()
    # Постоянную клавиатуру вешаем на отдельное сообщение: у карточки уже есть
    # инлайн-кнопки, а двух разметок в одном сообщении быть не может.
    await send(t(lang, "saved"), reply_markup=profile_reply_keyboard(lang))
    await send(render_profile(lang, profile), reply_markup=profile_keyboard(lang, profile))


async def _save_city_and_radius(
    user: User, lang: str, state: FSMContext, send: Sender
) -> None:
    """Сохраняет новый город (с нормализацией) и радиус, сбрасывает seen."""
    data = await state.get_data()
    city = str(data.get("city") or "").strip()
    km = parse_search_radius(data.get("search_radius"))
    profile = await get_user(user.id)
    first = str((profile or {}).get("first_name") or "")
    last = str((profile or {}).get("last_name") or "")
    normalized = await _normalize_identity(first, last, city)
    await _patch_profile(
        user,
        lang,
        state,
        send,
        {
            "city": city,
            "city_de": normalized["city_de"] or city,
            "search_radius": km,
            "first_name": normalized["first_name_latin"] or (profile or {}).get("first_name"),
            "last_name": normalized["last_name_latin"] or (profile or {}).get("last_name"),
        },
        reset_seen=True,
    )


async def _begin_field_edit(
    send: Sender, state: FSMContext, lang: str, field: str
) -> None:
    """Запускает вопрос только по выбранному полю готовой анкеты."""
    await state.clear()
    await state.update_data(language=lang)

    if field == "name":
        await prompt_missing_names(send, state, lang, announce=False)
        return
    if field == "gender":
        await prompt_missing_gender(send, state, lang, announce=False)
        return
    if field == "city":
        await state.set_state(OnboardingStates.city)
        await state.update_data(language=lang, city_only=True)
        await send(t(lang, "ask_city"))
        return
    if field == "radius":
        await state.set_state(OnboardingStates.radius)
        await state.update_data(language=lang, radius_only=True)
        await send(t(lang, "ask_radius"), reply_markup=radius_keyboard(lang))
        return
    if field == "budget":
        await state.set_state(OnboardingStates.budget)
        await state.update_data(language=lang, budget_only=True)
        await send(t(lang, "ask_budget"))
        return
    if field == "rooms":
        await state.set_state(OnboardingStates.rooms)
        await state.update_data(language=lang, rooms_only=True)
        await send(t(lang, "ask_rooms"))
        return
    if field == "sqm":
        await prompt_missing_sqm(send, state, lang, announce=False)
        return
    if field == "household":
        await prompt_missing_household(send, state, lang, announce=False)
        return
    if field == "wbs":
        await state.set_state(OnboardingStates.wbs)
        await state.update_data(language=lang, wbs_only=True)
        await send(t(lang, "ask_wbs"), reply_markup=yes_no_keyboard(lang))
        return
    if field == "jobcenter":
        await state.set_state(OnboardingStates.jobcenter)
        await state.update_data(language=lang, jobcenter_only=True)
        await send(t(lang, "ask_jobcenter"), reply_markup=yes_no_keyboard(lang))
        return
    if field == "pets":
        await state.set_state(OnboardingStates.pets)
        await state.update_data(language=lang, pets_only=True)
        await send(t(lang, "ask_pets"), reply_markup=yes_no_keyboard(lang))
        return
    if field == "income":
        await state.set_state(OnboardingStates.income)
        await state.update_data(language=lang, income_only=True)
        await send(t(lang, "ask_income"), reply_markup=skip_income_keyboard(lang))
        return
    if field == "notes":
        await state.set_state(OnboardingStates.custom_notes)
        await state.update_data(language=lang, notes_only=True)
        await send(t(lang, "ask_notes"), reply_markup=skip_keyboard(lang))


# --------------------------------------------------------------------------- #
# Точка входа: /start и кнопка «Открыть анкету»
# --------------------------------------------------------------------------- #
async def show_saved_or_start_survey(
    user: User, send: Sender, state: FSMContext
) -> None:
    """Карточка анкеты, если она есть, иначе первый шаг опроса."""
    profile = await get_user(user.id)

    if profile and profile.get("city"):
        lang = str(profile.get("language") or DEFAULT_LANG)
        await state.clear()
        await send(t(lang, "your_profile"), reply_markup=profile_reply_keyboard(lang))
        if await prompt_missing_profile_fields(send, state, lang, profile):
            return
        await send(
            render_profile(lang, profile), reply_markup=profile_keyboard(lang, profile)
        )
        return

    await send(WELCOME_TEXT)
    await _start_survey(send, state)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Показывает анкету, если она уже есть, иначе запускает опрос."""
    user = message.from_user
    if user is None:
        return

    logger.info("Команда /start от пользователя %s", user.id)
    await show_saved_or_start_survey(user, message.answer, state)


@router.message(F.text.in_(PROFILE_BUTTON_TEXTS))
async def button_open_profile(message: Message, state: FSMContext) -> None:
    """Кнопка под полем ввода: открывает сохранённую анкету."""
    user = message.from_user
    if user is None:
        return
    logger.info("Кнопка «Открыть анкету» от пользователя %s", user.id)
    await show_saved_or_start_survey(user, message.answer, state)


@router.callback_query(F.data == CB_NEW_PROFILE)
async def new_profile(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Кнопка «Создать новую анкету» — единственный способ перезапустить опрос."""
    profile = await get_user(callback.from_user.id)
    lang = str(profile.get("language") or DEFAULT_LANG) if profile else None

    await drop_keyboard(callback)
    await _start_survey(sender(callback, bot), state, lang)
    await callback.answer()


@router.callback_query(F.data == CB_EDIT_MENU)
async def open_edit_menu(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Показывает список полей, которые можно поменять по одному."""
    profile = await get_user(callback.from_user.id)
    lang = str(profile.get("language") or DEFAULT_LANG) if profile else DEFAULT_LANG
    await state.clear()
    await drop_keyboard(callback)
    await sender(callback, bot)(t(lang, "ask_edit_field"), reply_markup=edit_field_keyboard(lang))
    await callback.answer()


@router.callback_query(F.data == CB_EDIT_BACK)
async def cancel_edit_menu(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Возвращает карточку анкеты без изменений."""
    profile = await get_user(callback.from_user.id)
    lang = str(profile.get("language") or DEFAULT_LANG) if profile else DEFAULT_LANG
    await state.clear()
    await drop_keyboard(callback)
    send = sender(callback, bot)
    if profile is None:
        await send(t(lang, "no_profile"))
        await callback.answer()
        return
    await send(render_profile(lang, profile), reply_markup=profile_keyboard(lang, profile))
    await callback.answer()


@router.callback_query(F.data.startswith(CB_EDIT_PREFIX))
async def choose_edit_field(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    """Запускает вопрос по выбранному полю."""
    field = (callback.data or "").removeprefix(CB_EDIT_PREFIX)
    if field not in EDITABLE_FIELDS:
        await callback.answer()
        return

    profile = await get_user(callback.from_user.id)
    lang = str(profile.get("language") or DEFAULT_LANG) if profile else DEFAULT_LANG
    send = sender(callback, bot)
    if profile is None:
        await send(t(lang, "no_profile"))
        await callback.answer()
        return

    await drop_keyboard(callback)
    await _begin_field_edit(send, state, lang, field)
    await callback.answer()


# --------------------------------------------------------------------------- #
# Смена языка у готовой анкеты: ответы не сбрасываются
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == CB_LANG_MENU)
async def open_language_menu(callback: CallbackQuery, bot: Bot) -> None:
    """Показывает список языков для уже заполненной анкеты."""
    await drop_keyboard(callback)
    await sender(callback, bot)(
        CHOOSE_LANGUAGE, reply_markup=language_keyboard(CB_SETLANG_PREFIX)
    )
    await callback.answer()


@router.callback_query(F.data.startswith(CB_SETLANG_PREFIX))
async def set_language(callback: CallbackQuery, bot: Bot) -> None:
    """Меняет только язык интерфейса и перерисовывает карточку."""
    lang = (callback.data or "").removeprefix(CB_SETLANG_PREFIX)
    if lang not in LANGUAGES:
        await callback.answer()
        return

    send = sender(callback, bot)
    profile = await get_user(callback.from_user.id)
    if profile is None:
        await send(t(lang, "no_profile"))
        await callback.answer()
        return

    await update_user_language(callback.from_user.id, lang)
    profile["language"] = lang

    await drop_keyboard(callback)
    await send(t(lang, "lang_changed"))
    await send(render_profile(lang, profile), reply_markup=profile_keyboard(lang, profile))
    await callback.answer()


@router.callback_query(F.data == CB_AUTO_SEARCH)
async def toggle_auto_search_button(callback: CallbackQuery, bot: Bot) -> None:
    """Включает или выключает фоновый поиск и перерисовывает карточку."""
    profile = await get_user(callback.from_user.id)
    if profile is None:
        await callback.answer()
        return

    lang = str(profile.get("language") or DEFAULT_LANG)
    enabled = not bool(profile.get("is_auto_search_enabled", True))
    await toggle_auto_search(callback.from_user.id, enabled)
    profile["is_auto_search_enabled"] = enabled

    text = render_profile(lang, profile)
    markup = profile_keyboard(lang, profile)
    message = callback.message
    if isinstance(message, Message):
        try:
            await message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest:
            await sender(callback, bot)(text, reply_markup=markup)
    else:
        await sender(callback, bot)(text, reply_markup=markup)

    toast = t(
        lang,
        "auto_search_enabled_toast" if enabled else "auto_search_disabled_toast",
    )
    await callback.answer(toast)


# --------------------------------------------------------------------------- #
# Шаг 1: язык
# --------------------------------------------------------------------------- #
@router.callback_query(OnboardingStates.language, F.data.startswith(CB_LANG_PREFIX))
async def choose_language(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    """Запоминает язык и спрашивает пол."""
    lang = (callback.data or "").removeprefix(CB_LANG_PREFIX)
    if lang not in LANGUAGES:
        await callback.answer()
        return

    await state.update_data(language=lang)

    await drop_keyboard(callback)
    await state.set_state(OnboardingStates.gender)
    await sender(callback, bot)(t(lang, "ask_gender"), reply_markup=gender_keyboard(lang))
    await callback.answer()


# --------------------------------------------------------------------------- #
# Шаг 2: пол
# --------------------------------------------------------------------------- #
@router.callback_query(
    OnboardingStates.gender, F.data.in_({CB_GENDER_MALE, CB_GENDER_FEMALE})
)
async def process_gender(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Фиксирует пол заявителя и либо сохраняет его, либо спрашивает имя."""
    lang = await _lang_of(state)
    gender = "male" if callback.data == CB_GENDER_MALE else "female"
    await drop_keyboard(callback)

    data = await state.get_data()
    if data.get("gender_only"):
        await _save_gender(callback.from_user, gender, lang, state, sender(callback, bot))
        await callback.answer()
        return

    await state.update_data(applicant_gender=gender)
    await state.set_state(OnboardingStates.first_name)
    await sender(callback, bot)(t(lang, "ask_first_name"))
    await callback.answer()


# --------------------------------------------------------------------------- #
# Шаг 2: имя
# --------------------------------------------------------------------------- #
@router.message(OnboardingStates.first_name, F.text)
async def process_first_name(message: Message, state: FSMContext) -> None:
    """Принимает имя — кириллица допустима, в базу уйдёт латиница."""
    lang = await _lang_of(state)
    name = (message.text or "").strip()
    if not is_valid_name(name):
        await message.answer(
            t(lang, "err_name", min=NAME_MIN_LEN, max=NAME_MAX_LEN)
        )
        return

    await state.update_data(first_name=name)
    await state.set_state(OnboardingStates.last_name)
    await message.answer(t(lang, "ask_last_name"))


# --------------------------------------------------------------------------- #
# Шаг 3: фамилия
# --------------------------------------------------------------------------- #
@router.message(OnboardingStates.last_name, F.text)
async def process_last_name(message: Message, state: FSMContext) -> None:
    """Принимает фамилию и либо сохраняет её в старую анкету, либо спрашивает город."""
    lang = await _lang_of(state)
    name = (message.text or "").strip()
    if not is_valid_name(name):
        await message.answer(
            t(lang, "err_name", min=NAME_MIN_LEN, max=NAME_MAX_LEN)
        )
        return

    await state.update_data(last_name=name)
    data = await state.get_data()
    if data.get("names_only") and message.from_user is not None:
        await _save_names(message.from_user, lang, state, message.answer)
        return

    await state.set_state(OnboardingStates.city)
    await message.answer(t(lang, "ask_city"))


# --------------------------------------------------------------------------- #
# Шаг 4: город
# --------------------------------------------------------------------------- #
@router.message(OnboardingStates.city, F.text)
async def process_city(message: Message, state: FSMContext) -> None:
    """Принимает название города и спрашивает радиус поиска."""
    lang = await _lang_of(state)
    city = (message.text or "").strip()

    if not is_valid_city(city):
        await message.answer(
            t(lang, "err_city", min=CITY_MIN_LEN, max=CITY_MAX_LEN)
        )
        return

    await state.update_data(city=city)
    await state.set_state(OnboardingStates.radius)
    await message.answer(t(lang, "ask_radius"), reply_markup=radius_keyboard(lang))


# --------------------------------------------------------------------------- #
# Шаг 5: радиус
# --------------------------------------------------------------------------- #
@router.callback_query(OnboardingStates.radius, F.data.startswith(CB_RADIUS_PREFIX))
async def process_radius(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Фиксирует Umkreis и спрашивает бюджет."""
    raw = (callback.data or "").removeprefix(CB_RADIUS_PREFIX)
    try:
        km = int(raw)
    except ValueError:
        await callback.answer()
        return
    if km not in SEARCH_RADII:
        await callback.answer()
        return

    lang = await _lang_of(state)
    await state.update_data(search_radius=km)
    await drop_keyboard(callback)
    data = await state.get_data()
    send = sender(callback, bot)

    if data.get("city_only") and callback.from_user:
        await _save_city_and_radius(callback.from_user, lang, state, send)
        await callback.answer()
        return
    if data.get("radius_only") and callback.from_user:
        await _patch_profile(
            callback.from_user, lang, state, send, {"search_radius": km}
        )
        await callback.answer()
        return

    await state.set_state(OnboardingStates.budget)
    await send(t(lang, "ask_budget"))
    await callback.answer()


# --------------------------------------------------------------------------- #
# Шаг 6: бюджет
# --------------------------------------------------------------------------- #
@router.message(OnboardingStates.budget, F.text)
async def process_budget(message: Message, state: FSMContext) -> None:
    """Принимает максимальный бюджет Warmmiete в евро."""
    lang = await _lang_of(state)
    budget = parse_amount(message.text or "")

    if budget is None:
        await message.answer(t(lang, "err_number"))
        return
    if not BUDGET_MIN <= budget <= BUDGET_MAX:
        await message.answer(
            t(lang, "err_budget_range", min=BUDGET_MIN, max=BUDGET_MAX)
        )
        return

    await state.update_data(budget_max=budget)
    data = await state.get_data()
    if data.get("budget_only") and message.from_user is not None:
        await _patch_profile(
            message.from_user, lang, state, message.answer, {"budget_max": budget}
        )
        return

    await state.set_state(OnboardingStates.rooms)
    await message.answer(t(lang, "ask_rooms"))


# --------------------------------------------------------------------------- #
# Шаг 4: комнаты
# --------------------------------------------------------------------------- #
@router.message(OnboardingStates.rooms, F.text)
async def process_rooms(message: Message, state: FSMContext) -> None:
    """Принимает минимальное количество комнат (допускается дробное)."""
    lang = await _lang_of(state)
    rooms = parse_number(message.text or "")

    if rooms is None:
        await message.answer(t(lang, "err_number"))
        return
    if not ROOMS_MIN <= rooms <= ROOMS_MAX:
        await message.answer(
            t(lang, "err_rooms_range", min=_format_rooms(ROOMS_MIN), max=_format_rooms(ROOMS_MAX))
        )
        return

    await state.update_data(rooms_min=rooms)
    data = await state.get_data()
    if data.get("rooms_only") and message.from_user is not None:
        await _patch_profile(
            message.from_user, lang, state, message.answer, {"rooms_min": rooms}
        )
        return

    await state.set_state(OnboardingStates.sqm_min)
    await message.answer(t(lang, "ask_sqm_min"))


# --------------------------------------------------------------------------- #
# Шаг 5: минимальная площадь
# --------------------------------------------------------------------------- #
@router.message(OnboardingStates.sqm_min, F.text)
async def process_sqm_min(message: Message, state: FSMContext) -> None:
    """Принимает нижнюю границу Wohnfläche."""
    lang = await _lang_of(state)
    area = parse_sqm(message.text or "")

    if area is None or not SQM_MIN <= area <= SQM_MAX:
        await message.answer(
            t(
                lang,
                "err_sqm_range",
                min=_format_rooms(SQM_MIN),
                max=_format_rooms(SQM_MAX),
            )
        )
        return

    await state.update_data(sqm_min=area)
    await state.set_state(OnboardingStates.sqm_max)
    await message.answer(
        t(lang, "ask_sqm_max"), reply_markup=unlimited_sqm_keyboard(lang)
    )


# --------------------------------------------------------------------------- #
# Шаг 6: максимальная площадь
# --------------------------------------------------------------------------- #
async def _accept_sqm_max(
    area: float | None, state: FSMContext, send: Sender, user: User | None
) -> None:
    """Общая запись верхней границы: число или «без ограничений»."""
    lang = await _lang_of(state)
    data = await state.get_data()
    minimum = data.get("sqm_min")
    if area is not None and minimum is not None and area < float(minimum):
        await send(t(lang, "err_sqm_max_low", min=_format_rooms(float(minimum))))
        return
    if area is not None and not SQM_MIN <= area <= SQM_MAX:
        await send(
            t(
                lang,
                "err_sqm_range",
                min=_format_rooms(SQM_MIN),
                max=_format_rooms(SQM_MAX),
            )
        )
        return

    await state.update_data(sqm_max=area)
    if data.get("sqm_only") and user is not None:
        await _save_sqm(user, lang, state, send)
        return

    await state.set_state(OnboardingStates.household)
    await send(t(lang, "ask_household"))


@router.message(OnboardingStates.sqm_max, F.text)
async def process_sqm_max(message: Message, state: FSMContext) -> None:
    """Принимает верхнюю границу Wohnfläche числом."""
    lang = await _lang_of(state)
    area = parse_sqm(message.text or "")
    if area is None:
        await message.answer(t(lang, "err_number"))
        return
    await _accept_sqm_max(area, state, message.answer, message.from_user)


@router.callback_query(OnboardingStates.sqm_max, F.data == CB_SQM_UNLIMITED)
async def skip_sqm_max(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Кнопка «Без ограничений» — верхнюю границу не ставим."""
    await drop_keyboard(callback)
    await _accept_sqm_max(None, state, sender(callback, bot), callback.from_user)
    await callback.answer()


# --------------------------------------------------------------------------- #
# Шаг 7: сколько человек будет жить
# --------------------------------------------------------------------------- #
@router.message(OnboardingStates.household, F.text)
async def process_household(message: Message, state: FSMContext) -> None:
    """Принимает число жильцов: арендодатели почти всегда спрашивают Personenanzahl."""
    lang = await _lang_of(state)
    people = parse_count(message.text or "")

    if people is None or not HOUSEHOLD_MIN <= people <= HOUSEHOLD_MAX:
        await message.answer(
            t(lang, "err_household_range", min=HOUSEHOLD_MIN, max=HOUSEHOLD_MAX)
        )
        return

    data = await state.get_data()
    if people == 1:
        await state.update_data(household_size=1, household_type="single")
        if data.get("household_only") and message.from_user is not None:
            await _save_household(
                message.from_user, 1, lang, state, message.answer, household_type="single"
            )
            return
        await state.set_state(OnboardingStates.wbs)
        await message.answer(t(lang, "ask_wbs"), reply_markup=yes_no_keyboard(lang))
        return

    await state.update_data(household_size=people)
    await state.set_state(OnboardingStates.household_type)
    await message.answer(
        t(lang, "ask_household_type"), reply_markup=household_type_keyboard(lang)
    )


@router.callback_query(
    OnboardingStates.household_type, F.data.in_(set(HTYPE_CALLBACKS))
)
async def process_household_type(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    """Фиксирует состав жильцов и продолжает анкету или сохраняет её."""
    kind = HTYPE_CALLBACKS.get(callback.data or "")
    if kind is None:
        await callback.answer()
        return

    lang = await _lang_of(state)
    await state.update_data(household_type=kind)
    await drop_keyboard(callback)
    data = await state.get_data()
    try:
        people = int(data.get("household_size") or 0)
    except (TypeError, ValueError):
        people = parse_count(str(data.get("household_size") or "")) or 0

    if (data.get("household_only") or data.get("household_type_only")) and callback.from_user:
        if not people:
            profile = await get_user(callback.from_user.id)
            people = int((profile or {}).get("household_size") or 0)
        await _save_household(
            callback.from_user,
            people,
            lang,
            state,
            sender(callback, bot),
            household_type=kind,
        )
        await callback.answer()
        return

    await state.set_state(OnboardingStates.wbs)
    await sender(callback, bot)(t(lang, "ask_wbs"), reply_markup=yes_no_keyboard(lang))
    await callback.answer()


# --------------------------------------------------------------------------- #
# Шаг 8: WBS
# --------------------------------------------------------------------------- #
@router.callback_query(OnboardingStates.wbs, F.data.in_({CB_YES, CB_NO}))
async def process_wbs(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Фиксирует наличие WBS и спрашивает про Jobcenter."""
    lang = await _lang_of(state)
    has_wbs = callback.data == CB_YES
    await state.update_data(has_wbs=has_wbs)
    await drop_keyboard(callback)
    data = await state.get_data()
    if data.get("wbs_only") and callback.from_user:
        await _patch_profile(
            callback.from_user, lang, state, sender(callback, bot), {"has_wbs": has_wbs}
        )
        await callback.answer()
        return

    await state.set_state(OnboardingStates.jobcenter)
    await sender(callback, bot)(
        t(lang, "ask_jobcenter"), reply_markup=yes_no_keyboard(lang)
    )
    await callback.answer()


# --------------------------------------------------------------------------- #
# Шаг 9: Jobcenter
# --------------------------------------------------------------------------- #
@router.callback_query(OnboardingStates.jobcenter, F.data.in_({CB_YES, CB_NO}))
async def process_jobcenter(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    """Фиксирует оплату через Jobcenter и либо продолжает опрос, либо сохраняет."""
    lang = await _lang_of(state)
    uses_jobcenter = callback.data == CB_YES
    await state.update_data(uses_jobcenter=uses_jobcenter)
    await drop_keyboard(callback)

    data = await state.get_data()
    if data.get("support_only"):
        await _save_support_flags(callback.from_user, lang, state, sender(callback, bot))
        await callback.answer()
        return
    if data.get("jobcenter_only") and callback.from_user:
        await _patch_profile(
            callback.from_user,
            lang,
            state,
            sender(callback, bot),
            {"uses_jobcenter": uses_jobcenter},
        )
        await callback.answer()
        return

    await state.set_state(OnboardingStates.pets)
    await sender(callback, bot)(
        t(lang, "ask_pets"), reply_markup=yes_no_keyboard(lang)
    )
    await callback.answer()


# --------------------------------------------------------------------------- #
# Шаг 10: домашние животные
# --------------------------------------------------------------------------- #
@router.callback_query(OnboardingStates.pets, F.data.in_({CB_YES, CB_NO}))
async def process_pets(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Фиксирует наличие животных и спрашивает про доход."""
    lang = await _lang_of(state)
    has_pets = callback.data == CB_YES
    await state.update_data(has_pets=has_pets)
    await drop_keyboard(callback)
    data = await state.get_data()
    if data.get("pets_only") and callback.from_user:
        await _patch_profile(
            callback.from_user, lang, state, sender(callback, bot), {"has_pets": has_pets}
        )
        await callback.answer()
        return

    await state.set_state(OnboardingStates.income)
    await sender(callback, bot)(
        t(lang, "ask_income"), reply_markup=skip_income_keyboard(lang)
    )
    await callback.answer()


# --------------------------------------------------------------------------- #
# Шаг 11: чистый доход
# --------------------------------------------------------------------------- #
@router.message(OnboardingStates.income, F.text)
async def process_income(message: Message, state: FSMContext) -> None:
    """Принимает чистый доход семьи в месяц."""
    lang = await _lang_of(state)
    income = parse_amount(message.text or "")

    if income is None:
        await message.answer(t(lang, "err_number"))
        return
    if not INCOME_MIN <= income <= INCOME_MAX:
        await message.answer(
            t(lang, "err_income_range", min=INCOME_MIN, max=INCOME_MAX)
        )
        return

    await state.update_data(net_income=income)
    data = await state.get_data()
    if data.get("income_only") and message.from_user is not None:
        await _patch_profile(
            message.from_user, lang, state, message.answer, {"net_income": income}
        )
        return

    await state.set_state(OnboardingStates.custom_notes)
    await message.answer(t(lang, "ask_notes"), reply_markup=skip_keyboard(lang))


@router.callback_query(OnboardingStates.income, F.data == CB_SKIP_INCOME)
async def skip_income(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Пропуск дохода: в анкете остаётся NULL, письмо не будет называть сумму."""
    lang = await _lang_of(state)
    await state.update_data(net_income=None)
    await drop_keyboard(callback)
    data = await state.get_data()
    if data.get("income_only") and callback.from_user:
        await _patch_profile(
            callback.from_user,
            lang,
            state,
            sender(callback, bot),
            {"net_income": None},
        )
        await callback.answer()
        return

    await state.set_state(OnboardingStates.custom_notes)
    await sender(callback, bot)(
        t(lang, "ask_notes"), reply_markup=skip_keyboard(lang)
    )
    await callback.answer()


# --------------------------------------------------------------------------- #
# Шаг 12: свободные пожелания
# --------------------------------------------------------------------------- #
@router.message(OnboardingStates.custom_notes, F.text)
async def process_notes(message: Message, state: FSMContext) -> None:
    """Принимает свободные пожелания и завершает анкету."""
    lang = await _lang_of(state)
    notes = (message.text or "").strip()

    if len(notes) > NOTES_MAX_LEN:
        await message.answer(t(lang, "err_notes_long", max=NOTES_MAX_LEN))
        return

    await state.update_data(custom_notes=notes or None)
    data = await state.get_data()
    if data.get("notes_only") and message.from_user is not None:
        await _patch_profile(
            message.from_user,
            lang,
            state,
            message.answer,
            {"custom_notes": notes or None},
        )
        return

    if message.from_user is not None:
        await _finish_onboarding(state, message.from_user, message.answer)


@router.callback_query(OnboardingStates.custom_notes, F.data == CB_SKIP)
async def skip_notes(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Кнопка «Пропустить» — завершает анкету без пожеланий."""
    lang = await _lang_of(state)
    await state.update_data(custom_notes=None)
    await drop_keyboard(callback)
    data = await state.get_data()
    if data.get("notes_only"):
        await _patch_profile(
            callback.from_user,
            lang,
            state,
            sender(callback, bot),
            {"custom_notes": None},
        )
        await callback.answer()
        return

    await _finish_onboarding(state, callback.from_user, sender(callback, bot))
    await callback.answer()


# --------------------------------------------------------------------------- #
# Подсказки при неожиданном вводе.
# Регистрируются последними: срабатывают, только если не подошёл ни один
# из обработчиков конкретного шага выше.
# --------------------------------------------------------------------------- #
@router.message(
    StateFilter(
        OnboardingStates.first_name,
        OnboardingStates.last_name,
        OnboardingStates.city,
        OnboardingStates.budget,
        OnboardingStates.rooms,
        OnboardingStates.sqm_min,
        OnboardingStates.sqm_max,
        OnboardingStates.household,
        OnboardingStates.income,
        OnboardingStates.custom_notes,
    )
)
async def expect_text(message: Message, state: FSMContext) -> None:
    """Пользователь прислал фото, стикер или контакт вместо текста."""
    await message.answer(t(await _lang_of(state), "err_text_only"))


@router.message(
    StateFilter(
        OnboardingStates.language,
        OnboardingStates.gender,
        OnboardingStates.radius,
        OnboardingStates.household_type,
        OnboardingStates.wbs,
        OnboardingStates.jobcenter,
        OnboardingStates.pets,
    )
)
async def expect_button(message: Message, state: FSMContext) -> None:
    """На шагах с кнопками текстовый ответ не принимается."""
    await message.answer(t(await _lang_of(state), "use_buttons"))


@router.callback_query(
    StateFilter(None),
    F.data.in_(
        {
            CB_YES,
            CB_NO,
            CB_SKIP,
            CB_SKIP_INCOME,
            CB_SQM_UNLIMITED,
            CB_GENDER_MALE,
            CB_GENDER_FEMALE,
            *HTYPE_CALLBACKS,
        }
    )
    | F.data.startswith(CB_LANG_PREFIX)
    | F.data.startswith(CB_RADIUS_PREFIX),
)
async def stale_callback(callback: CallbackQuery, bot: Bot) -> None:
    """Кнопка из старого сообщения после перезапуска бота: состояние потеряно.

    Фильтр по своим callback_data обязателен: без него роутер поглощал бы
    колбэки всех модулей, которые появятся позже.
    """
    await sender(callback, bot)(t(DEFAULT_LANG, "session_lost"))
    await callback.answer()
