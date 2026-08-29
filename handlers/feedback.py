"""Обратная связь от пользователей бета-теста."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from config import get_settings
from database import get_user
from handlers.common import sender
from services.alerts import send_admin_alert
from texts import DEFAULT_LANG, t

logger = logging.getLogger(__name__)

router = Router(name="feedback")

CB_FEEDBACK_HINT: str = "feedback:hint"


@router.message(Command("feedback"))
async def cmd_feedback(message: Message, bot: Bot) -> None:
    """Пересылает текст после /feedback администратору."""
    user = message.from_user
    if user is None:
        return

    profile = await get_user(user.id)
    lang = str(profile.get("language") or DEFAULT_LANG) if profile else DEFAULT_LANG

    raw = message.text or ""
    parts = raw.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(t(lang, "feedback_usage"))
        return

    feedback_text = parts[1].strip()
    username = user.username or "—"
    admin_message = (
        f"📩 [FEEDBACK] От пользователя @{username} (ID: {user.id}):\n"
        f"{feedback_text}"
    )
    await send_admin_alert(bot, admin_message)
    logger.info("Feedback от пользователя %s (%d символов)", user.id, len(feedback_text))
    await message.answer(t(lang, "feedback_thanks"))


@router.callback_query(F.data == CB_FEEDBACK_HINT)
async def feedback_hint(callback: CallbackQuery, bot: Bot) -> None:
    """Кнопка «Обратная связь» под анкетой — подсказка по /feedback."""
    profile = await get_user(callback.from_user.id)
    lang = str(profile.get("language") or DEFAULT_LANG) if profile else DEFAULT_LANG
    await sender(callback, bot)(t(lang, "feedback_usage"))
    await callback.answer()
