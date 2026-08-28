"""Помощники, общие для роутеров."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

logger = logging.getLogger(__name__)

# Способ отправить сообщение: message.answer или bot.send_message с готовым chat_id.
Sender = Callable[..., Awaitable[Any]]


def sender(event: Message | CallbackQuery, bot: Bot) -> Sender:
    """Возвращает способ ответить пользователю для сообщения или колбэка.

    У нажатия на кнопку исходное сообщение может быть недоступно (слишком
    старое), поэтому в таком случае пишем напрямую в чат.
    """
    if isinstance(event, Message):
        return event.answer
    if isinstance(event.message, Message):
        return event.message.answer
    return partial(bot.send_message, event.from_user.id)


async def delete_quietly(message: Message | None) -> None:
    """Удаляет служебное сообщение, не поднимая шум, если это не вышло."""
    if message is None:
        return
    try:
        await message.delete()
    except TelegramBadRequest:
        # Сообщение уже удалено или старше 48 часов — не повод прерывать поиск.
        logger.debug("Не удалось удалить сообщение %s", message.message_id)


async def drop_keyboard(callback: CallbackQuery) -> None:
    """Убирает кнопки у обработанного сообщения, чтобы их нельзя было нажать дважды."""
    if not isinstance(callback.message, Message):
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        # Сообщение слишком старое или уже без клавиатуры — не критично.
        logger.debug(
            "Не удалось убрать клавиатуру у сообщения %s",
            callback.message.message_id,
        )
