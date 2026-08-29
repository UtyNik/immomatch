"""Фоновый автопоиск объявлений по расписанию (APScheduler)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import get_settings
from database import get_auto_search_users, toggle_auto_search
from services.user_limits import BETA_AI_LETTERS_DAILY
from texts import DEFAULT_LANG, t

logger = logging.getLogger(__name__)

_JOB_ID: Final[str] = "auto_search"


def _profile_ready(profile: dict[str, Any]) -> bool:
    """Фоновый поиск не спрашивает недостающие поля — неполную анкету пропускаем."""
    if not (
        profile.get("city")
        and profile.get("first_name")
        and profile.get("last_name")
        and profile.get("applicant_gender")
        and profile.get("sqm_min") is not None
        and profile.get("household_size") is not None
        and profile.get("has_wbs") is not None
        and profile.get("uses_jobcenter") is not None
    ):
        return False
    try:
        people = int(profile["household_size"])
    except (TypeError, ValueError):
        return False
    if people > 1 and not profile.get("household_type"):
        return False
    return True


async def _notify_match(
    bot: Bot,
    profile: dict[str, Any],
    apartment: dict[str, Any],
    verdict: dict[str, Any],
) -> None:
    """Пуш с карточкой и Anschreiben. Если пользователь заблокировал бота — выключаем автопоиск."""
    # Импорт здесь, чтобы пакет services не тянул хэндлеры при загрузке ai_agent.
    from handlers.search import listing_url_keyboard, render_listing_card

    user_id = int(profile["user_id"])
    lang = str(profile.get("language") or DEFAULT_LANG)
    card = render_listing_card(lang, apartment, verdict)
    text = f"{t(lang, 'auto_search_found')}\n\n{card}"
    try:
        await bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=listing_url_keyboard(lang, str(apartment["link"])),
            disable_web_page_preview=True,
        )
    except TelegramForbiddenError:
        logger.info(
            "Пользователь %s заблокировал бота — автопоиск выключен", user_id
        )
        await toggle_auto_search(user_id, False)
    except TelegramBadRequest as error:
        logger.warning(
            "Не удалось отправить пуш пользователю %s: %s", user_id, error
        )


async def _search_one_user(bot: Bot, profile: dict[str, Any]) -> None:
    """Один пользователь: лимит AI, все провайдеры, фильтр, первое совпадение — пуш."""
    from handlers.search import find_first_match

    user_id = int(profile["user_id"])
    if not _profile_ready(profile):
        logger.info("Автопоиск: пользователь %s пропущен — анкета неполная", user_id)
        return

    result = await find_first_match(profile)
    if result.failure == "limit":
        logger.info("Автопоиск: пользователь %s исчерпал лимит AI", user_id)
        return
    if result.failure == "beta_letters":
        logger.info(
            "Автопоиск: пользователь %s — лимит Anschreiben бета (%d/день), поиск без AI",
            user_id,
            BETA_AI_LETTERS_DAILY,
        )
        return
    if result.failure == "empty":
        logger.info(
            "Автопоиск: пользователь %s, пустая выдача по всем площадкам",
            user_id,
        )
        return
    if result.failure:
        logger.info(
            "Автопоиск: пользователь %s, сбой %s (%s)",
            user_id,
            result.failure,
            result.failure_detail or "",
        )
        return
    if result.apartment is None or result.verdict is None:
        logger.info(
            "Автопоиск: пользователь %s без совпадений (проверено %d)",
            user_id,
            result.checked,
        )
        return

    await _notify_match(bot, profile, result.apartment, result.verdict)


async def run_background_search(bot: Bot) -> None:
    """Обходит пользователей с включённым автопоиском и шлёт пуш при совпадении.

    Для каждого пользователя — тот же конвейер, что и у ручного поиска
    (оркестратор провайдеров + фильтр + load_details + AI): на первом
    `match` рассылка этому пользователю останавливается до следующего тика.

    Несколько пользователей идут параллельно (asyncio + Semaphore), а не
    строго по очереди: при 10 пользователях и concurrency=3 цикл занимает
    примерно втрое меньше времени, чем последовательный обход.
    """
    users = await get_auto_search_users()
    concurrency = max(1, int(get_settings().auto_search_concurrency))
    logger.info(
        "Автопоиск: старт, пользователей %d, параллельно %d",
        len(users),
        concurrency,
    )

    if not users:
        logger.info("Автопоиск: цикл завершён")
        return

    semaphore = asyncio.Semaphore(concurrency)

    async def run_one(profile: dict[str, Any]) -> None:
        async with semaphore:
            try:
                await _search_one_user(bot, profile)
            except Exception:
                logger.exception(
                    "Автопоиск для пользователя %s сорвался",
                    profile.get("user_id"),
                )

    await asyncio.gather(*(run_one(profile) for profile in users))

    logger.info("Автопоиск: цикл завершён")


def create_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Создаёт и запускает планировщик с интервалом из настроек."""
    settings = get_settings()
    minutes = max(1, int(settings.auto_search_interval_minutes))
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_background_search,
        trigger=IntervalTrigger(minutes=minutes),
        args=[bot],
        id=_JOB_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Планировщик автопоиска запущен: каждые %d мин", minutes)
    return scheduler


def shutdown_scheduler(scheduler: AsyncIOScheduler | None) -> None:
    """Останавливает планировщик, не дожидаясь текущего тика."""
    if scheduler is None or not scheduler.running:
        return
    scheduler.shutdown(wait=False)
    logger.info("Планировщик автопоиска остановлен")
