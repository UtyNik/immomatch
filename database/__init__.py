"""Пакет доступа к базе данных."""

from database.db import (
    clear_seen_apartments,
    count_ai_calls_today,
    get_auto_search_users,
    get_user,
    init_db,
    is_apartment_seen,
    mark_apartment_seen,
    mark_deep_search_done,
    register_ai_call,
    save_user_profile,
    toggle_auto_search,
    update_user_language,
    upsert_listings,
)

__all__ = [
    "init_db",
    "save_user_profile",
    "get_user",
    "get_auto_search_users",
    "toggle_auto_search",
    "update_user_language",
    "clear_seen_apartments",
    "is_apartment_seen",
    "mark_apartment_seen",
    "mark_deep_search_done",
    "count_ai_calls_today",
    "register_ai_call",
    "upsert_listings",
]
