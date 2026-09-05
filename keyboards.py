"""Клавиатуры, общие для нескольких роутеров."""

from __future__ import annotations

from typing import Final

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from texts import LANGUAGES, t

CB_SEARCH: Final[str] = "start_search"
CB_SEARCH_NEXT: Final[str] = "search_next"
CB_AUTO_SEARCH: Final[str] = "auto_search_toggle"
CB_GEN_LETTER_PREFIX: Final[str] = "gen_letter:"
CB_TEMPLATE_EDIT: Final[str] = "template:edit"
CB_TEMPLATE_CLEAR: Final[str] = "template:clear"
CB_TEMPLATE_SKIP: Final[str] = "template:skip"

# Постоянная клавиатура присылает обычный текст, поэтому хэндлер узнаёт нажатие
# по подписи. Язык интерфейса пользователя тут неизвестен — держим все варианты.
PROFILE_BUTTON_TEXTS: Final[frozenset[str]] = frozenset(
    t(code, "btn_open_profile") for code in LANGUAGES
)
# Старая кнопка «Искать жильё» ещё может висеть у тех, кто не открывал анкету
# после замены: пусть она по-прежнему запускает поиск.
SEARCH_BUTTON_TEXTS: Final[frozenset[str]] = frozenset(
    t(code, "btn_search") for code in LANGUAGES
)


def profile_reply_keyboard(lang: str) -> ReplyKeyboardMarkup:
    """Кнопка «Открыть анкету» под полем ввода."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t(lang, "btn_open_profile"))]],
        resize_keyboard=True,
        is_persistent=True,
    )
