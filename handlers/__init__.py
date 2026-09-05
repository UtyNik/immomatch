"""Пакет с роутерами aiogram."""

from aiogram import Router

from handlers.admin import router as admin_router
from handlers.feedback import router as feedback_router
from handlers.letter import router as letter_router
from handlers.onboarding import router as onboarding_router
from handlers.search import router as search_router

__all__ = [
    "onboarding_router",
    "search_router",
    "letter_router",
    "feedback_router",
    "admin_router",
    "get_routers",
]


def get_routers() -> list[Router]:
    """Список всех роутеров приложения в порядке подключения к диспетчеру.

    Порядок важен: апдейт получает первый роутер с подходящим хэндлером.
    Поиск идёт первым, потому что онбординг перехватывает любой текст внутри
    своих состояний — иначе кнопку поиска нельзя было бы нажать посреди анкеты.
    """
    return [
        search_router,
        letter_router,
        feedback_router,
        admin_router,
        onboarding_router,
    ]
