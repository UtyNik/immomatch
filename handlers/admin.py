"""Панель администратора (/admin)."""

from __future__ import annotations

import logging
from collections import Counter

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config import get_settings
from database.db import (
    count_anschreiben_today_all,
    count_anschreiben_total,
    count_auto_search_active,
    count_listings_total,
    count_users_total,
)
from services.alerts import parser_alerts_last_24h

logger = logging.getLogger(__name__)

router = Router(name="admin")


def _is_admin(user_id: int | None) -> bool:
    admin_id = get_settings().admin_telegram_id
    return admin_id is not None and user_id == admin_id


def _format_alert_status() -> str:
    alerts = parser_alerts_last_24h()
    if not alerts:
        return "за последние 24 ч алертов не было ✅"
    by_platform = Counter(str(item["platform"]) for item in alerts)
    parts = [f"{platform}: {count}" for platform, count in sorted(by_platform.items())]
    latest = alerts[-1]
    return (
        f"за 24 ч — {len(alerts)} алерт(ов) ({', '.join(parts)})\n"
        f"последний: {latest['platform']} — {latest['issue']}"
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    """Статистика бета-теста — только для ADMIN_TELEGRAM_ID."""
    user = message.from_user
    if user is None or not _is_admin(user.id):
        return

    users_total = await count_users_total()
    auto_active = await count_auto_search_active()
    listings_total = await count_listings_total()
    letters_total = await count_anschreiben_total()
    letters_today = await count_anschreiben_today_all()
    alert_status = _format_alert_status()

    text = (
        "📊 <b>ImmoMatch — панель администратора</b>\n\n"
        f"👤 Пользователей: <b>{users_total}</b>\n"
        f"🔄 Активных автопоисков: <b>{auto_active}</b>\n\n"
        f"🏠 Объявлений в базе: <b>{listings_total}</b>\n\n"
        f"✉️ Anschreiben: <b>{letters_total}</b> всего / "
        f"<b>{letters_today}</b> за сегодня (UTC)\n\n"
        f"⚠️ CAPTCHA / блокировки: {alert_status}"
    )
    await message.answer(text)
    logger.info("Админ %s запросил /admin", user.id)
