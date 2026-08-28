"""Точка входа ImmoMatch AI: инициализация бота и запуск long polling."""

from __future__ import annotations

import asyncio
import logging
import socket
import sys
from typing import Final

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from pydantic import ValidationError

from config import Settings, get_settings
from database import init_db
from handlers import get_routers
from services.alerts import init_alert_bot
from services.scheduler import create_scheduler, shutdown_scheduler

logger = logging.getLogger(__name__)

# Порт на localhost используется как признак «бот уже запущен»: занять его
# дважды нельзя, а при завершении процесса он освобождается сам — в отличие
# от pid-файла, который остаётся висеть после аварийной остановки.
_SINGLE_INSTANCE_PORT: Final[int] = 47653


def acquire_single_instance_lock() -> socket.socket | None:
    """Возвращает сокет-замок или None, если бот уже запущен в другом процессе.

    Два polling-клиента одного бота Telegram не обслуживает: второй отбирает
    апдейты у первого, и оба сыпят Conflict-ошибками.
    """
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", _SINGLE_INSTANCE_PORT))
    except OSError:
        lock.close()
        return None
    return lock


async def setup_bot_commands(bot: Bot) -> None:
    """Заполняет меню команд рядом с полем ввода.

    Telegram выбирает набор по языку клиента, а не по языку анкеты, поэтому
    описания задаются для каждого языка отдельно.
    """
    menus: Final[dict[str | None, tuple[str, str]]] = {
        None: ("Profile", "Find a home"),
        "uk": ("Анкета", "Знайти житло"),
        "ru": ("Анкета", "Найти жильё"),
    }
    for language_code, (start_title, search_title) in menus.items():
        await bot.set_my_commands(
            [
                BotCommand(command="start", description=start_title),
                BotCommand(command="search", description=search_title),
            ],
            language_code=language_code,
        )


def setup_logging() -> None:
    """Базовая настройка логирования."""
    # Консоль Windows по умолчанию использует cp1252 и падает на кириллице/эмодзи.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


def create_dispatcher() -> Dispatcher:
    """Создаёт диспетчер и подключает к нему все роутеры."""
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_routers(*get_routers())
    return dp


def create_bot(settings: Settings) -> Bot:
    """Создаёт экземпляр бота с HTML-разметкой по умолчанию."""
    return Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def main() -> None:
    """Запускает бота в режиме long polling."""
    setup_logging()

    try:
        settings = get_settings()
    except ValidationError as error:
        # Типичная причина — строка в .env без префикса "ИМЯ=" или опечатка в имени.
        missing = ", ".join(str(err["loc"][0]).upper() for err in error.errors())
        logger.error(
            "В .env не найдены переменные: %s. Каждая строка должна иметь вид ИМЯ=значение.",
            missing,
        )
        raise SystemExit(1) from None

    instance_lock = acquire_single_instance_lock()
    if instance_lock is None:
        logger.error(
            "Бот уже запущен в другом окне терминала. Остановите тот процесс "
            "(Ctrl+C) — иначе Telegram будет отдавать апдейты только одному из них."
        )
        raise SystemExit(1)

    settings.prepare_storage()  # гарантируем наличие каталога для SQLite
    await init_db()

    bot = create_bot(settings)
    dp = create_dispatcher()
    scheduler = None

    try:
        me = await bot.get_me()
        logger.info("Бот @%s запущен, БД: %s", me.username, settings.db_file)
        await setup_bot_commands(bot)
        scheduler = create_scheduler(bot)
        init_alert_bot(bot)

        # Пропускаем накопившиеся апдейты, чтобы не отвечать на старые сообщения.
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except TelegramUnauthorizedError:
        # Traceback здесь бесполезен: причина всегда в значении BOT_TOKEN.
        logger.error(
            "Telegram отклонил токен. Проверь BOT_TOKEN в .env: "
            "получи актуальный у @BotFather (/mybots -> API Token или /newbot)."
        )
        raise SystemExit(1) from None
    finally:
        shutdown_scheduler(scheduler)
        await bot.session.close()
        instance_lock.close()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # SystemExit намеренно не перехватываем: он несёт ненулевой код возврата.
        logger.info("Выход по сигналу пользователя")
