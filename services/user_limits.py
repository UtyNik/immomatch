"""Лимиты использования для закрытого бета-теста."""

from __future__ import annotations

from typing import Final

from database.db import count_anschreiben_today, get_user

# Потолок сгенерированных Anschreiben на пользователя в сутки (UTC).
BETA_AI_LETTERS_DAILY: Final[int] = 15
# Один профиль = один пресет автопоиска на пользователя.
MAX_AUTO_SEARCH_PRESETS: Final[int] = 1


async def letters_used_today(user_id: int) -> int:
    """Сколько Anschreiben уже отправлено пользователю сегодня."""
    return await count_anschreiben_today(user_id)


async def can_generate_letter(user_id: int) -> bool:
    """Можно ли ещё генерировать Anschreiben сегодня."""
    return await letters_used_today(user_id) < BETA_AI_LETTERS_DAILY


async def can_enable_auto_search(user_id: int) -> bool:
    """Разрешён ли автопоиск: не более одного активного пресета на пользователя."""
    profile = await get_user(user_id)
    if profile is None:
        return True
    if not profile.get("is_auto_search_enabled"):
        return True
    # Уже включён — выключить можно всегда; включить повторно тоже (тот же пресет).
    return True


async def auto_search_preset_active(user_id: int) -> bool:
    """Есть ли у пользователя активный пресет автопоиска."""
    profile = await get_user(user_id)
    return bool(profile and profile.get("is_auto_search_enabled"))
