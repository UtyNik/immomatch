"""Ленивая генерация Anschreiben по кнопке под карточкой."""

from __future__ import annotations

import logging
from typing import Final

from aiogram import F, Router, html
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import get_listing, get_user, register_letter_call
from keyboards import CB_GEN_LETTER_PREFIX
from services import generate_anschreiben
from services.user_limits import BETA_AI_LETTERS_DAILY, can_generate_letter
from texts import DEFAULT_LANG, t

logger = logging.getLogger(__name__)
router = Router(name="letter")

MAX_LETTER: Final[int] = 2500


def _letter_keyboard(lang: str, link: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if link:
        builder.button(text=t(lang, "btn_open_listing"), url=link)
    builder.adjust(1)
    return builder.as_markup()


def _shorten(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


@router.callback_query(F.data.startswith(CB_GEN_LETTER_PREFIX))
async def callback_generate_letter(callback: CallbackQuery) -> None:
    """Генерирует Anschreiben только для выбранного объявления."""
    user = callback.from_user
    profile = await get_user(user.id)
    lang = str((profile or {}).get("language") or DEFAULT_LANG)

    raw = (callback.data or "").removeprefix(CB_GEN_LETTER_PREFIX)
    source, sep, external_id = raw.partition(":")
    if not sep or not source or not external_id:
        await callback.answer(t(lang, "letter_bad_payload"), show_alert=True)
        return

    if profile is None or not profile.get("city"):
        await callback.answer(t(lang, "no_profile"), show_alert=True)
        return

    if not await can_generate_letter(user.id):
        await callback.answer(
            t(lang, "beta_letter_limit_toast", limit=BETA_AI_LETTERS_DAILY),
            show_alert=True,
        )
        return

    apartment = await get_listing(source, external_id)
    if apartment is None:
        await callback.answer(t(lang, "letter_listing_missing"), show_alert=True)
        return

    await callback.answer(t(lang, "letter_generating"))

    # Лимит списываем до вызова модели, как у AI-оценки.
    await register_letter_call(user.id)

    result = await generate_anschreiben(profile, apartment)
    letter = _shorten(str(result.get("anschreiben") or ""), MAX_LETTER)
    if result.get("error") or not letter:
        detail = html.quote(str(result.get("reason") or ""))
        text = t(lang, "letter_failed", error=detail)
        message = callback.message
        if isinstance(message, Message):
            await message.answer(text)
        return

    body = (
        f"{t(lang, 'card_letter')}\n"
        f"<code>{html.quote(letter)}</code>\n\n"
        f"{t(lang, 'letter_copy_hint')}"
    )
    message = callback.message
    if isinstance(message, Message):
        await message.answer(
            body,
            reply_markup=_letter_keyboard(lang, str(apartment.get("link") or "")),
            disable_web_page_preview=True,
        )
    logger.info(
        "Пользователь %s: письмо для %s:%s, длина %d",
        user.id,
        source,
        external_id,
        len(letter),
    )
