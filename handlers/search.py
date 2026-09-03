"""Поиск жилья: парсер, AI-фильтрация и карточка подходящего объявления."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final

from aiogram import Bot, F, Router, html
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, User
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import get_settings
from database import (
    count_ai_calls_today,
    get_user,
    is_apartment_seen,
    mark_apartment_seen,
    mark_deep_search_done,
    register_ai_call,
    save_user_profile,
    upsert_listings,
)
from handlers.common import Sender, delete_quietly, sender
from handlers.onboarding import prompt_missing_profile_fields
from keyboards import CB_SEARCH, CB_SEARCH_NEXT, SEARCH_BUTTON_TEXTS, profile_reply_keyboard
from scrapers import FOLLOWUP_SEARCH_PAGES, INITIAL_SEARCH_PAGES
from services import (
    analyze_apartment_and_generate_letter,
    gender_restriction_reason,
    listing_type_reason,
    shared_wg_reason,
)
from services.parsers.base import legacy_dict_storage_id
from services.deduplicator import apartment_to_listing_data, is_duplicate_listing
from services.lease_filter import temporary_lease_reason
from services.listing_price import format_price_line
from services.listing_time import format_published_ago
from services.search_orchestrator import get_search_orchestrator
from services.user_limits import BETA_AI_LETTERS_DAILY, can_generate_letter
from services.translator import normalize_and_translate_user_input
from texts import DEFAULT_LANG, t
from validators import (
    MIN_PLAUSIBLE_RENT,
    area_below_minimum,
    city_mismatch_reason,
    kalt_only_budget_reason,
    parse_search_radius,
)

logger = logging.getLogger(__name__)

router = Router(name="search")

# На одной странице поиска Kleinanzeigen около 25 карточек. Для первого
# обход анкеты грузим детали со всех собранных страниц, иначе 2-я
# так и не дойдёт до Warmmiete.
DETAILS_PER_PAGE: Final[int] = 25
# Сколько объявлений максимум отправляем в модель за один поиск. Перебор идёт
# до первого совпадения, но каждая проверка платная, поэтому он ограничен.
MAX_AI_CHECKS: Final[int] = 5
# Ограничения на длину частей карточки: у сообщения Telegram лимит 4096 символов.
MAX_TITLE: Final[int] = 200
MAX_REASON: Final[int] = 700
MAX_LETTER: Final[int] = 2500


@dataclass(slots=True)
class FirstMatchResult:
    """Итог одного прохода поиска до первого совпадения."""

    apartment: dict[str, Any] | None = None
    verdict: dict[str, Any] | None = None
    checked: int = 0
    filtered: int = 0
    # scraper — сайт не отдал объявления; empty — пустая выдача;
    # ai — сбой модели; limit — суточный потолок.
    failure: str | None = None
    failure_detail: str | None = None


def _shorten(text: str, limit: int) -> str:
    """Обрезает длинный текст, оставляя многоточие."""
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _format_rooms(value: Any) -> str | None:
    """3.0 печатаем как «3», 2.5 оставляем как есть."""
    if value is None:
        return None
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def hard_filter_reason(profile: dict[str, Any], apartment: dict[str, Any]) -> str | None:
    """Почему объявление не проходит по цифрам, или None, если проходит."""
    city = city_mismatch_reason(profile, apartment)
    if city is not None:
        return city

    budget = profile.get("budget_max")
    price = apartment.get("price")
    if price is not None and price < MIN_PLAUSIBLE_RENT:
        return f"цена {price} € слишком низкая для долгосрочной аренды"
    if budget is not None and price is not None and price > budget:
        return f"цена {price} € > бюджет {budget} €"

    kalt_reason = kalt_only_budget_reason(
        budget, price, apartment.get("price_kind")
    )
    if kalt_reason is not None:
        return kalt_reason

    rooms_min = profile.get("rooms_min")
    rooms = apartment.get("rooms")
    if rooms_min is not None and rooms is not None and rooms < rooms_min:
        return f"комнат {rooms} < минимум {rooms_min}"

    area = apartment.get("sqm")
    if area_below_minimum(profile.get("sqm_min"), area):
        return f"площадь {area} м² < минимум {profile.get('sqm_min')} м²"
    sqm_max = profile.get("sqm_max")
    if sqm_max is not None and sqm_max > 0 and area is not None and area > sqm_max:
        return f"площадь {area} м² > максимум {sqm_max} м²"

    listing_kind = listing_type_reason(apartment)
    if listing_kind is not None:
        return listing_kind

    lease = temporary_lease_reason(apartment)
    if lease is not None:
        return lease

    wg = shared_wg_reason(profile, apartment)
    if wg is not None:
        return wg

    gender = gender_restriction_reason(profile, apartment)
    if gender is not None:
        return gender

    return None


def passes_hard_filters(profile: dict[str, Any], apartment: dict[str, Any]) -> bool:
    """Отсеивает заведомо неподходящее до обращения к модели.

    Проверяются только измеримые условия. Неизвестные значения не считаются
    нарушением: пусть решает AI, у него есть текст объявления.
    """
    return hard_filter_reason(profile, apartment) is None


# --------------------------------------------------------------------------- #
# Клавиатуры и карточка
# --------------------------------------------------------------------------- #
def listing_keyboard(lang: str, link: str) -> InlineKeyboardMarkup:
    """Ссылка на объявление и переход к следующему подходящему."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t(lang, "btn_open"), url=link)
    builder.button(text=t(lang, "btn_skip_next"), callback_data=CB_SEARCH_NEXT)
    builder.adjust(1)
    return builder.as_markup()


def listing_url_keyboard(lang: str, link: str) -> InlineKeyboardMarkup:
    """Только ссылка на объявление — для пуша автопоиска и после «Искать дальше»."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t(lang, "btn_open_listing"), url=link)
    return builder.as_markup()


def link_only_keyboard(lang: str, link: str) -> InlineKeyboardMarkup:
    """Та же карточка после нажатия «Искать дальше»: остаётся только ссылка."""
    return listing_url_keyboard(lang, link)


def _source_label(lang: str, source: str) -> str:
    """Человекочитаемое имя площадки для плашки в карточке."""
    key = f"source_{source}"
    label = t(lang, key)
    return label if label != key else source.replace("_", " ").title()


def render_listing_card(
    lang: str, apartment: dict[str, Any], verdict: dict[str, Any]
) -> str:
    """Собирает карточку объявления с вердиктом AI и письмом."""
    facts: list[str] = []
    price_line = format_price_line(lang, apartment)
    if price_line:
        facts.append(price_line)
    elif apartment.get("price") is not None:
        facts.append(f"💶 {int(apartment['price'])} €")
    rooms = _format_rooms(apartment.get("rooms"))
    if rooms:
        facts.append(f"🚪 {rooms}")
    area = _format_rooms(apartment.get("sqm"))
    if area:
        facts.append(f"📐 {area} m²")
    if apartment.get("address"):
        facts.append(f"📍 {html.quote(str(apartment['address']))}")

    source = str(apartment.get("source") or "kleinanzeigen")
    source_label = _source_label(lang, source)
    title = _shorten(str(apartment.get("title") or ""), MAX_TITLE)
    verdict_title = t(lang, "card_match_yes" if verdict["match"] else "card_match_no")

    lines = [f"🏠 [{source_label}] <b>{html.quote(title)}</b>"]
    if facts:
        lines.append(" · ".join(facts))
    published_line = format_published_ago(apartment.get("published_at"), lang)
    if published_line:
        lines.append(published_line)
    lines.append("")
    lines.append(verdict_title)

    reason = _shorten(str(verdict.get("reason") or ""), MAX_REASON)
    if reason:
        lines.append(f"🤖 <i>{html.quote(reason)}</i>")

    letter = _shorten(str(verdict.get("anschreiben") or ""), MAX_LETTER)
    if letter:
        lines.append("")
        lines.append(t(lang, "card_letter"))
        # <code> позволяет скопировать письмо одним нажатием.
        lines.append(f"<code>{html.quote(letter)}</code>")

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Основной сценарий
# --------------------------------------------------------------------------- #
async def _collect_candidates(
    user_id: int,
    profile: dict[str, Any],
    listings: list[dict[str, Any]],
    *,
    stage: str,
    log_reasons: bool = False,
) -> tuple[list[dict[str, Any]], int, int]:
    """Оставляет непоказанные объявления, прошедшие проверку по цифрам.

    Возвращает кандидатов, число отсеянных по цифрам и число уже виденных.
    """
    candidates: list[dict[str, Any]] = []
    filtered = 0
    seen = 0

    for listing in listings:
        storage_id = legacy_dict_storage_id(listing)
        if not storage_id or storage_id.endswith(":"):
            continue
        if await is_apartment_seen(user_id, storage_id):
            seen += 1
            continue
        reason = hard_filter_reason(profile, listing)
        if reason is not None:
            # Такие объявления не помечаем показанными: если пользователь
            # поднимет бюджет, они снова попадут в выборку.
            filtered += 1
            if log_reasons:
                log_msg = (
                    f"Отброшено ({reason})"
                    if reason.startswith("Временная аренда")
                    else reason
                )
                logger.info(
                    "Отсев [%s] %s (%s): %s",
                    stage,
                    storage_id,
                    listing.get("title") or "",
                    log_msg,
                )
            continue
        candidates.append(listing)

    logger.info(
        "Пользователь %s [%s]: прошло %d, отсеяно по цифрам %d, уже видели %d",
        user_id,
        stage,
        len(candidates),
        filtered,
        seen,
    )
    return candidates, filtered, seen


async def _prepare_search_location(profile: dict[str, Any]) -> tuple[str, int]:
    """Немецкий город и радиус для URL. Если city_de ещё нет — нормализуем и пишем в БД."""
    city_de = str(profile.get("city_de") or "").strip()
    if not city_de:
        try:
            normalized = await normalize_and_translate_user_input(
                str(profile.get("first_name") or ""),
                str(profile.get("last_name") or ""),
                str(profile.get("city") or ""),
            )
            city_de = normalized["city_de"] or str(profile.get("city") or "")
            profile["city_de"] = city_de
            if not profile.get("first_name") and normalized["first_name_latin"]:
                profile["first_name"] = normalized["first_name_latin"]
            if not profile.get("last_name") and normalized["last_name_latin"]:
                profile["last_name"] = normalized["last_name_latin"]
            await save_user_profile(profile)
        except Exception:
            logger.exception("Не удалось нормализовать город для поиска")
            city_de = str(profile.get("city") or "")
    return city_de, parse_search_radius(profile.get("search_radius"))


async def find_first_match(profile: dict[str, Any]) -> FirstMatchResult:
    """Ищет первое подходящее объявление: карточки, фильтр по цифрам, AI.

    Не пишет в Telegram: вызывающий сам решает, показать карточку, пуш
    или промолчать. Модель оценивает объявление один раз: совпадение
    больше не показывается даже после смены анкеты, отказ можно
    пересмотреть, если изменились бюджет или состав жильцов.
    """
    user_id = int(profile["user_id"])
    settings = get_settings()
    used = await count_ai_calls_today(user_id)

    city, radius = await _prepare_search_location(profile)
    deep = not bool(profile.get("deep_search_done"))
    pages = INITIAL_SEARCH_PAGES if deep else FOLLOWUP_SEARCH_PAGES
    orchestrator = get_search_orchestrator()
    search_criteria = {
        "city_de": city,
        "city": profile.get("city"),
        "radius": radius,
        "budget_max": profile.get("budget_max"),
        "rooms_min": profile.get("rooms_min"),
        "sqm_min": profile.get("sqm_min"),
        "max_pages": pages,
        "bundesland": profile.get("bundesland"),
        "federated_state_id": profile.get("federated_state_id"),
        "restrict_to_bundesland": bool(profile.get("restrict_to_bundesland")),
    }
    listings, provider_errors = await orchestrator.fetch_all(search_criteria)
    await upsert_listings(listings)

    if not listings and provider_errors:
        detail = "; ".join(provider_errors)
        logger.error("Поиск для пользователя %s не удался: %s", user_id, detail)
        return FirstMatchResult(failure="scraper", failure_detail=detail)

    if deep and listings:
        await mark_deep_search_done(user_id)
        profile["deep_search_done"] = True
        logger.info(
            "Пользователь %s: первый обход анкеты, %d стр., %d карточек",
            user_id,
            pages,
            len(listings),
        )
    elif deep:
        logger.warning(
            "Пользователь %s: глубокий поиск не зафиксирован — карточек нет",
            user_id,
        )

    if not listings:
        return FirstMatchResult(failure="empty")

    # Первый проход — по данным карточки. Цена в списке это Kaltmiete, то есть
    # не больше Warmmiete: если уже она выше бюджета, страницу качать незачем.
    # Комнаты и площадь на карточке часто неизвестны — такие не режем.
    candidates, filtered, _seen = await _collect_candidates(
        user_id, profile, listings, stage="карточка", log_reasons=True
    )
    candidates = candidates[: DETAILS_PER_PAGE * pages]

    if candidates:
        # Второй проход — после загрузки страниц: там настоящая Warmmiete,
        # Wohnfläche и число комнат.
        try:
            await orchestrator.load_details(candidates)
        except Exception:
            logger.exception("Не удалось догрузить страницы объявлений")
        detailed, filtered_detailed, _ = await _collect_candidates(
            user_id,
            profile,
            candidates,
            stage="страница",
            log_reasons=True,
        )
        candidates = detailed
        filtered += filtered_detailed

    if not candidates:
        return FirstMatchResult(filtered=filtered)

    deduped: list[dict[str, Any]] = []
    for apartment in candidates:
        if await is_duplicate_listing(apartment_to_listing_data(apartment)):
            filtered += 1
            await mark_apartment_seen(
                user_id,
                legacy_dict_storage_id(apartment),
                was_match=False,
            )
            continue
        deduped.append(apartment)
    candidates = deduped

    if not candidates:
        return FirstMatchResult(filtered=filtered)

    if not await can_generate_letter(user_id):
        logger.info(
            "Пользователь %s: лимит Anschreiben бета-теста (%d/день)",
            user_id,
            BETA_AI_LETTERS_DAILY,
        )
        return FirstMatchResult(filtered=filtered, failure="beta_letters")

    if not settings.ai_budget_left(used):
        logger.info("Пользователь %s исчерпал дневной лимит AI", user_id)
        return FirstMatchResult(filtered=filtered, failure="limit")

    checked = 0
    for apartment in candidates[:MAX_AI_CHECKS]:
        if not settings.ai_budget_left(used):
            return FirstMatchResult(checked=checked, filtered=filtered, failure="limit")

        # Списываем попытку до вызова: иначе сбой на стороне OpenAI позволял бы
        # дёргать платный запрос бесконечно.
        await register_ai_call(user_id)
        used += 1
        checked += 1

        verdict = await analyze_apartment_and_generate_letter(profile, apartment)
        if verdict.get("error"):
            return FirstMatchResult(
                checked=checked,
                filtered=filtered,
                failure="ai",
                failure_detail=str(verdict.get("reason") or ""),
            )

        await mark_apartment_seen(
            user_id,
            legacy_dict_storage_id(apartment),
            was_match=bool(verdict["match"]),
        )

        if not verdict["match"]:
            logger.info(
                "Объявление %s отклонено: %s",
                legacy_dict_storage_id(apartment),
                verdict.get("reason"),
            )
            continue

        logger.info(
            "Пользователь %s: подходящее найдено после %d проверок",
            user_id,
            checked,
        )
        return FirstMatchResult(
            apartment=apartment,
            verdict=verdict,
            checked=checked,
            filtered=filtered,
        )

    logger.info("Пользователь %s: подходящих нет, проверено %d", user_id, checked)
    return FirstMatchResult(checked=checked, filtered=filtered)


async def run_search(user: User, send: Sender, state: FSMContext) -> None:
    """Ищет первое подходящее объявление и показывает его карточку.

    Перебор идёт до первого совпадения. Неподходящие объявления пользователю
    не показываются вовсе, но помечаются просмотренными, чтобы не платить за
    их повторную оценку.
    """
    profile = await get_user(user.id)
    if profile is None or not profile.get("city"):
        await send(t(DEFAULT_LANG, "no_profile"))
        return

    lang = str(profile.get("language") or DEFAULT_LANG)
    if await prompt_missing_profile_fields(send, state, lang, profile):
        return

    status: Message | None = await send(
        t(lang, "search_status"), reply_markup=profile_reply_keyboard(lang)
    )
    try:
        result = await find_first_match(profile)
    finally:
        await delete_quietly(status)

    if result.failure == "limit":
        await send(t(lang, "ai_limit_reached", limit=get_settings().ai_daily_limit))
        return
    if result.failure == "beta_letters":
        await send(
            t(lang, "beta_letter_limit", limit=BETA_AI_LETTERS_DAILY),
            reply_markup=profile_reply_keyboard(lang),
        )
        return
    if result.failure == "scraper":
        await send(
            t(lang, "search_failed", error=html.quote(result.failure_detail or ""))
        )
        return
    if result.failure == "ai":
        await send(
            t(lang, "ai_failed", error=html.quote(result.failure_detail or ""))
        )
        return
    if result.failure == "empty":
        await send(t(lang, "search_no_match", checked=0))
        return

    if result.apartment is not None and result.verdict is not None:
        await send(
            render_listing_card(lang, result.apartment, result.verdict),
            reply_markup=listing_keyboard(lang, str(result.apartment["link"])),
            disable_web_page_preview=True,
        )
        return

    if result.checked == 0 and result.filtered == 0:
        await send(t(lang, "search_no_fresh"))
        return
    if result.checked == 0:
        await send(t(lang, "search_no_match", checked=0))
        return
    await send(t(lang, "search_no_match", checked=result.checked))


# --------------------------------------------------------------------------- #
# Точки входа: команда, кнопка под полем ввода и инлайн-кнопки
# --------------------------------------------------------------------------- #
@router.message(Command("search", "test_search"))
async def cmd_search(message: Message, state: FSMContext) -> None:
    """Команды /search и /test_search."""
    if message.from_user is not None:
        await run_search(message.from_user, message.answer, state)


@router.message(F.text.in_(SEARCH_BUTTON_TEXTS))
async def button_search(message: Message, state: FSMContext) -> None:
    """Кнопка под полем ввода: она присылает обычный текст."""
    if message.from_user is not None:
        await run_search(message.from_user, message.answer, state)


@router.callback_query(F.data == CB_SEARCH)
async def callback_search(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    """Кнопка поиска под карточкой анкеты."""
    # Отвечаем сразу: поиск занимает секунды, а «часики» у кнопки живут недолго.
    await callback.answer()
    await run_search(callback.from_user, sender(callback, bot), state)


@router.callback_query(F.data == CB_SEARCH_NEXT)
async def callback_search_next(
    callback: CallbackQuery, bot: Bot, state: FSMContext
) -> None:
    """Кнопка «Пропустить / Искать дальше» под карточкой объявления."""
    await callback.answer()

    # Убираем кнопку продолжения у показанной карточки, оставляя ссылку:
    # повторное нажатие запускало бы ещё один платный поиск.
    message = callback.message
    if isinstance(message, Message) and message.reply_markup is not None:
        link = next(
            (
                button.url
                for row in message.reply_markup.inline_keyboard
                for button in row
                if button.url
            ),
            None,
        )
        profile = await get_user(callback.from_user.id)
        lang = str((profile or {}).get("language") or DEFAULT_LANG)
        try:
            await message.edit_reply_markup(
                reply_markup=link_only_keyboard(lang, link) if link else None
            )
        except TelegramBadRequest:
            # Карточка слишком старая для редактирования — поиск всё равно идёт.
            logger.debug("Не удалось обновить клавиатуру карточки %s", message.message_id)

    await run_search(callback.from_user, sender(callback, bot), state)
