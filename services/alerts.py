"""Telegram-алерты администратору о сбоях парсеров."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Final

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from config import get_settings
from services.http_politeness import detect_block_reason

logger = logging.getLogger(__name__)

_ALERT_COOLDOWN_SEC: Final[int] = 3600
_alert_bot: Bot | None = None
_last_parser_alert: dict[str, float] = {}
_parser_alert_history: list[dict[str, str | float]] = []
_MAX_ALERT_HISTORY: Final[int] = 200


def init_alert_bot(bot: Bot) -> None:
    """Регистрирует экземпляр бота для фоновых алертов без явной передачи bot."""
    global _alert_bot
    _alert_bot = bot


def _alert_key(source_platform: str, issue: str) -> str:
    return f"{source_platform}:{issue.casefold()}"


def _cooldown_active(source_platform: str, issue: str) -> bool:
    key = _alert_key(source_platform, issue)
    last_sent = _last_parser_alert.get(key)
    if last_sent is None:
        return False
    return (time.monotonic() - last_sent) < _ALERT_COOLDOWN_SEC


def _mark_sent(source_platform: str, issue: str) -> None:
    _last_parser_alert[_alert_key(source_platform, issue)] = time.monotonic()


def _record_parser_alert(source_platform: str, issue: str, detail: str | None) -> None:
    """Сохраняет алерт для /admin (последние 24 ч)."""
    global _parser_alert_history
    _parser_alert_history.append(
        {
            "platform": source_platform,
            "issue": issue,
            "detail": detail or "",
            "ts": time.time(),
        }
    )
    if len(_parser_alert_history) > _MAX_ALERT_HISTORY:
        _parser_alert_history = _parser_alert_history[-_MAX_ALERT_HISTORY :]


def parser_alerts_last_24h() -> list[dict[str, str | float]]:
    """Алерты парсеров за последние 24 часа (UTC monotonic wall time)."""
    cutoff = time.time() - 86400
    return [item for item in _parser_alert_history if float(item["ts"]) >= cutoff]


async def send_admin_alert(bot: Bot | None, message: str) -> None:
    """Отправляет сообщение администратору, если задан ADMIN_TELEGRAM_ID."""
    settings = get_settings()
    admin_id = settings.admin_telegram_id
    if admin_id is None:
        return

    target_bot = bot or _alert_bot
    if target_bot is None:
        logger.debug("Алерт не отправлен: bot не инициализирован")
        return

    try:
        await target_bot.send_message(chat_id=admin_id, text=message)
    except TelegramForbiddenError:
        logger.warning("Администратор %s заблокировал бота — алерт не доставлен", admin_id)
    except TelegramBadRequest as error:
        logger.warning("Не удалось отправить алерт админу %s: %s", admin_id, error)


async def send_parser_alert(
    source_platform: str,
    issue: str,
    *,
    bot: Bot | None = None,
    detail: str | None = None,
) -> None:
    """Алерт о проблеме парсера с cooldown 1 ч на пару (площадка, тип)."""
    if _cooldown_active(source_platform, issue):
        logger.debug(
            "Алерт %s/%s подавлен cooldown (%ds)",
            source_platform,
            issue,
            _ALERT_COOLDOWN_SEC,
        )
        return

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    message = (
        f"⚠️ [ALERT] Проблема с парсером {source_platform}: "
        f"перехвачена {issue}. Время: {timestamp}"
    )
    if detail:
        message = f"{message}\n<i>{detail}</i>"

    await send_admin_alert(bot, message)
    _mark_sent(source_platform, issue)
    _record_parser_alert(source_platform, issue, detail)
    logger.info("Алерт админу: %s — %s", source_platform, issue)


async def alert_http_status(
    source_platform: str,
    status_code: int,
    *,
    bot: Bot | None = None,
    url: str | None = None,
) -> None:
    """Маппинг HTTP-кода на тип алерта."""
    if status_code == 403:
        issue = "403 Forbidden"
    elif status_code == 429:
        issue = "429 Too Many Requests"
    else:
        return
    detail = f"HTTP {status_code}" + (f" — {url}" if url else "")
    await send_parser_alert(source_platform, issue, bot=bot, detail=detail)


async def alert_blocked_html(
    source_platform: str,
    html: str,
    *,
    bot: Bot | None = None,
    context: str | None = None,
) -> None:
    """Алерт при CAPTCHA / блокировке в теле ответа."""
    reason = detect_block_reason(html) or "CAPTCHA"
    detail = context or f"HTML {len(html)} символов"
    await send_parser_alert(source_platform, reason, bot=bot, detail=detail)


async def alert_parse_failure(
    source_platform: str,
    *,
    bot: Bot | None = None,
    detail: str | None = None,
) -> None:
    """Алерт при фатальной ошибке разбора структуры страницы."""
    await send_parser_alert(
        source_platform,
        "ошибка парсинга",
        bot=bot,
        detail=detail,
    )
