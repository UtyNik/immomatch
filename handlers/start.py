"""Приветствие и вводная информация для закрытого бета-теста."""

from __future__ import annotations

from texts import DEFAULT_LANG, t


def beta_intro_text(lang: str | None = None) -> str:
    """Короткое описание бота для /start (мультиязычное)."""
    code = lang if lang in {"ua", "ru", "en"} else DEFAULT_LANG
    return t(code, "beta_intro")
