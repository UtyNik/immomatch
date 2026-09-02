"""Асинхронный слой доступа к SQLite поверх aiosqlite."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Final

import aiosqlite

from config import get_settings
from validators import parse_applicant_gender, parse_household_type, parse_search_radius

logger = logging.getLogger(__name__)

# user_id объявлен как INTEGER, а не BIGINT: только точное написание
# "INTEGER PRIMARY KEY" делает колонку псевдонимом rowid, а вмещает она
# 64-битное знаковое число — этого с запасом хватает для Telegram ID.
_CREATE_USERS_TABLE: Final[str] = """
CREATE TABLE IF NOT EXISTS users (
    user_id        INTEGER   PRIMARY KEY,
    username       TEXT,
    language       TEXT      NOT NULL DEFAULT 'ua',
    city           TEXT,
    budget_max     INTEGER,
    rooms_min      REAL,
    sqm_min        REAL,
    sqm_max        REAL,
    household_size INTEGER,
    has_wbs        INTEGER,
    uses_jobcenter INTEGER,
    has_pets               INTEGER,
    net_income             INTEGER,
    custom_notes           TEXT,
    first_name             TEXT,
    last_name              TEXT,
    city_de                TEXT,
    search_radius          INTEGER   NOT NULL DEFAULT 0,
    applicant_gender       TEXT,
    household_type         TEXT,
    is_active              INTEGER   NOT NULL DEFAULT 1,
    is_auto_search_enabled INTEGER   NOT NULL DEFAULT 1,
    deep_search_done       INTEGER   NOT NULL DEFAULT 0,
    created_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

# Колонки, добавленные к users после первого релиза. CREATE TABLE IF NOT EXISTS
# существующую таблицу не трогает, поэтому их приходится досоздавать вручную —
# иначе у тех, кто уже заполнил анкету, бот падал бы на запросах.
_USERS_ADDED_COLUMNS: Final[dict[str, str]] = {
    "household_size": "INTEGER",
    "has_wbs": "INTEGER",
    "uses_jobcenter": "INTEGER",
    "sqm_min": "REAL",
    "sqm_max": "REAL",
    "is_auto_search_enabled": "BOOLEAN DEFAULT 1",
    "first_name": "TEXT",
    "last_name": "TEXT",
    "city_de": "TEXT",
    "search_radius": "INTEGER NOT NULL DEFAULT 0",
    "applicant_gender": "TEXT",
    "household_type": "TEXT",
    "deep_search_done": "INTEGER NOT NULL DEFAULT 0",
}

# Составной первичный ключ сам защищает от дублей: повторная отметка того же
# объявления просто ничего не меняет.
_CREATE_SEEN_TABLE: Final[str] = """
CREATE TABLE IF NOT EXISTS seen_apartments (
    user_id      INTEGER   NOT NULL,
    apartment_id TEXT      NOT NULL,
    was_match    INTEGER   NOT NULL DEFAULT 0,
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, apartment_id)
)
"""

# Счётчик обращений к OpenAI по дням. День хранится строкой в UTC, чтобы
# лимит не сбрасывался дважды при переходе на летнее время.
_CREATE_AI_USAGE_TABLE: Final[str] = """
CREATE TABLE IF NOT EXISTS ai_usage (
    user_id INTEGER NOT NULL,
    day     TEXT    NOT NULL,
    calls   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
)
"""

_CREATE_LISTINGS_TABLE: Final[str] = """
CREATE TABLE IF NOT EXISTS listings (
    source      TEXT      NOT NULL,
    external_id TEXT      NOT NULL,
    url         TEXT      NOT NULL UNIQUE,
    title       TEXT,
    price       INTEGER,
    size_sqm    REAL,
    rooms       REAL,
    location    TEXT,
    image_url   TEXT,
    description TEXT      NOT NULL DEFAULT '',
    raw_data    TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source, external_id)
)
"""

_UPSERT_USER: Final[str] = """
INSERT INTO users (
    user_id, username, language, city, city_de, first_name, last_name,
    search_radius, budget_max, rooms_min, sqm_min, sqm_max, household_size,
    household_type, applicant_gender, has_wbs, uses_jobcenter, has_pets,
    net_income, custom_notes, is_active
) VALUES (
    :user_id, :username, :language, :city, :city_de, :first_name, :last_name,
    :search_radius, :budget_max, :rooms_min, :sqm_min, :sqm_max,
    :household_size, :household_type, :applicant_gender, :has_wbs,
    :uses_jobcenter, :has_pets, :net_income, :custom_notes, :is_active
)
ON CONFLICT(user_id) DO UPDATE SET
    username           = excluded.username,
    language           = excluded.language,
    city               = excluded.city,
    city_de            = excluded.city_de,
    first_name         = excluded.first_name,
    last_name          = excluded.last_name,
    search_radius      = excluded.search_radius,
    budget_max         = excluded.budget_max,
    rooms_min          = excluded.rooms_min,
    sqm_min            = excluded.sqm_min,
    sqm_max            = excluded.sqm_max,
    household_size     = excluded.household_size,
    household_type     = excluded.household_type,
    applicant_gender   = excluded.applicant_gender,
    has_wbs            = excluded.has_wbs,
    uses_jobcenter     = excluded.uses_jobcenter,
    has_pets           = excluded.has_pets,
    net_income         = excluded.net_income,
    custom_notes       = excluded.custom_notes,
    is_active          = excluded.is_active
"""

# Колонки, которые в SQLite лежат как 0/1, но наружу отдаются как bool.
_BOOL_COLUMNS: Final[tuple[str, ...]] = (
    "has_wbs",
    "uses_jobcenter",
    "has_pets",
    "is_active",
    "is_auto_search_enabled",
    "deep_search_done",
)


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Открывает соединение с БД и отдаёт строки в виде sqlite3.Row."""
    settings = get_settings()
    settings.prepare_storage()

    async with aiosqlite.connect(settings.db_file) as connection:
        connection.row_factory = aiosqlite.Row
        yield connection


def _to_optional_bool(value: Any) -> bool | None:
    """Приводит значение к bool, сохраняя None (ответ ещё не получен)."""
    return None if value is None else bool(value)


async def _add_missing_columns(db: aiosqlite.Connection) -> None:
    """Досоздаёт колонки, появившиеся в users после первого релиза."""
    async with db.execute("PRAGMA table_info(users)") as cursor:
        existing = {row["name"] for row in await cursor.fetchall()}

    for column, column_type in _USERS_ADDED_COLUMNS.items():
        if column in existing:
            continue
        try:
            await db.execute(f"ALTER TABLE users ADD COLUMN {column} {column_type}")
            logger.info("Таблица users дополнена колонкой %s", column)
        except Exception:
            # Повторный ALTER на уже существующую колонку SQLite отвергает.
            logger.debug("Колонка users.%s уже есть, ALTER пропущен", column)


async def _add_missing_seen_columns(db: aiosqlite.Connection) -> None:
    """Досоздаёт колонки seen_apartments, появившиеся после первого релиза."""
    async with db.execute("PRAGMA table_info(seen_apartments)") as cursor:
        existing = {row["name"] for row in await cursor.fetchall()}
    if "was_match" in existing:
        return
    await db.execute(
        "ALTER TABLE seen_apartments ADD COLUMN was_match INTEGER NOT NULL DEFAULT 0"
    )
    # Старые строки — и карточки, и отказы AI. Что из них уже отправили
    # пользователю, надёжно не восстановить, поэтому прячем все: важнее
    # не показать повторно объявление, на которое уже был отклик.
    await db.execute("UPDATE seen_apartments SET was_match = 1")
    logger.info("Таблица seen_apartments дополнена колонкой was_match")


async def _migrate_seen_storage_ids(db: aiosqlite.Connection) -> None:
    """Старые ID без префикса считаем Kleinanzeigen."""
    await db.execute(
        """
        UPDATE seen_apartments
        SET apartment_id = 'kleinanzeigen:' || apartment_id
        WHERE apartment_id NOT LIKE '%:%'
        """
    )


async def init_db() -> None:
    """Создаёт схему БД, если её ещё нет. Вызывается один раз при старте."""
    async with _connect() as db:
        await db.execute(_CREATE_USERS_TABLE)
        await db.execute(_CREATE_SEEN_TABLE)
        await db.execute(_CREATE_AI_USAGE_TABLE)
        await db.execute(_CREATE_LISTINGS_TABLE)
        await _add_missing_columns(db)
        await _add_missing_seen_columns(db)
        await _migrate_seen_storage_ids(db)
        # Кто уже искал жильё, тот не получает повторный обход на 2 страницы.
        await db.execute(
            """
            UPDATE users
            SET deep_search_done = 1
            WHERE COALESCE(deep_search_done, 0) = 0
              AND user_id IN (SELECT DISTINCT user_id FROM seen_apartments)
            """
        )
        await db.commit()
    logger.info("База данных готова: %s", get_settings().db_file)


async def save_user_profile(user_data: dict[str, Any]) -> None:
    """Создаёт или полностью обновляет анкету пользователя.

    Отсутствующие ключи сохраняются как NULL, поэтому словарь можно собирать
    из данных FSM, не заполняя все поля вручную.
    """
    user_id = user_data.get("user_id")
    if user_id is None:
        raise ValueError("save_user_profile: в user_data отсутствует user_id")

    params: dict[str, Any] = {
        "user_id": int(user_id),
        "username": user_data.get("username"),
        "language": user_data.get("language") or "ua",
        "city": user_data.get("city"),
        "city_de": (str(user_data["city_de"]).strip() or None)
        if user_data.get("city_de")
        else None,
        "first_name": (str(user_data["first_name"]).strip() or None)
        if user_data.get("first_name")
        else None,
        "last_name": (str(user_data["last_name"]).strip() or None)
        if user_data.get("last_name")
        else None,
        "search_radius": parse_search_radius(user_data.get("search_radius")),
        "applicant_gender": parse_applicant_gender(user_data.get("applicant_gender")),
        "household_type": parse_household_type(user_data.get("household_type")),
        "budget_max": user_data.get("budget_max"),
        "rooms_min": user_data.get("rooms_min"),
        "sqm_min": user_data.get("sqm_min"),
        # 0 с кнопки «без ограничений» храним как NULL: иначе фильтр
        # «площадь > 0» отрезал бы все объявления.
        "sqm_max": _to_optional_sqm_max(user_data.get("sqm_max")),
        "household_size": user_data.get("household_size"),
        "has_wbs": _to_int_flag(user_data.get("has_wbs")),
        "uses_jobcenter": _to_int_flag(user_data.get("uses_jobcenter")),
        "has_pets": _to_int_flag(user_data.get("has_pets")),
        "net_income": _to_optional_income(user_data.get("net_income")),
        "custom_notes": user_data.get("custom_notes"),
        "is_active": _to_int_flag(user_data.get("is_active", True)) or 0,
    }

    async with _connect() as db:
        await db.execute(_UPSERT_USER, params)
        await db.commit()
    logger.info("Анкета пользователя %s сохранена", params["user_id"])


def _row_to_profile(row: aiosqlite.Row) -> dict[str, Any]:
    """Превращает строку SQLite в словарь анкеты с bool-полями."""
    profile = dict(row)
    for column in _BOOL_COLUMNS:
        if column in profile:
            profile[column] = _to_optional_bool(profile.get(column))
    # Старое совмещённое поле больше не используется: WBS и Jobcenter —
    # разные сущности, и один ответ нельзя честно разложить на два.
    profile.pop("wbs_status", None)
    if profile.get("sqm_max") == 0:
        profile["sqm_max"] = None
    profile["net_income"] = _to_optional_income(profile.get("net_income"))
    if profile.get("is_auto_search_enabled") is None:
        profile["is_auto_search_enabled"] = True
    if profile.get("deep_search_done") is None:
        profile["deep_search_done"] = False
    if profile.get("search_radius") is None:
        profile["search_radius"] = 0
    else:
        profile["search_radius"] = parse_search_radius(profile.get("search_radius"))
    return profile


async def get_user(user_id: int) -> dict[str, Any] | None:
    """Возвращает анкету пользователя или None, если её ещё нет."""
    async with _connect() as db:
        async with db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

    if row is None:
        return None
    return _row_to_profile(row)


async def get_auto_search_users() -> list[dict[str, Any]]:
    """Активные пользователи с включённым автопоиском и заполненным городом."""
    async with _connect() as db:
        async with db.execute(
            """
            SELECT * FROM users
            WHERE is_active = 1
              AND COALESCE(is_auto_search_enabled, 1) = 1
              AND city IS NOT NULL
              AND TRIM(city) != ''
            """
        ) as cursor:
            rows = await cursor.fetchall()
    return [_row_to_profile(row) for row in rows]


async def toggle_auto_search(user_id: int, enabled: bool) -> None:
    """Включает или выключает фоновый поиск для пользователя."""
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET is_auto_search_enabled = ? WHERE user_id = ?",
            (int(bool(enabled)), user_id),
        )
        await db.commit()
    logger.info(
        "Автопоиск пользователя %s %s",
        user_id,
        "включён" if enabled else "выключен",
    )


async def clear_seen_apartments(user_id: int) -> None:
    """После смены условий поиска снова обходим выдачу, но не те же совпадения.

    Отказы AI стираем: при другом бюджете или составе жильцов они могут
    подойти. Карточки, которые уже ушли пользователю, оставляем — на них
    скорее всего уже был отклик.
    """
    async with _connect() as db:
        await db.execute(
            """
            DELETE FROM seen_apartments
            WHERE user_id = ? AND COALESCE(was_match, 0) = 0
            """,
            (user_id,),
        )
        await db.execute(
            "UPDATE users SET deep_search_done = 0 WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()


async def mark_deep_search_done(user_id: int) -> None:
    """После первого обхода выдачи дальше берём только первую страницу."""
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET deep_search_done = 1 WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()


async def update_user_language(user_id: int, lang: str) -> None:
    """Меняет язык интерфейса, не затрагивая остальные поля анкеты."""
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET language = ? WHERE user_id = ?", (lang, user_id)
        )
        await db.commit()


async def upsert_listings(listings: list[dict[str, Any]]) -> None:
    """Сохраняет или обновляет объявления из всех площадок."""
    if not listings:
        return

    async with _connect() as db:
        for item in listings:
            source = str(item.get("source") or "kleinanzeigen")
            external_id = str(item.get("external_id") or "")
            url = str(item.get("link") or "")
            if not external_id or not url:
                continue
            raw = item.get("raw_data")
            raw_json = json.dumps(raw, ensure_ascii=False) if raw is not None else None
            await db.execute(
                """
                INSERT INTO listings (
                    source, external_id, url, title, price, size_sqm, rooms,
                    location, image_url, description, raw_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, external_id) DO UPDATE SET
                    url = excluded.url,
                    title = excluded.title,
                    price = excluded.price,
                    size_sqm = excluded.size_sqm,
                    rooms = excluded.rooms,
                    location = excluded.location,
                    image_url = excluded.image_url,
                    description = excluded.description,
                    raw_data = excluded.raw_data,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    source,
                    external_id,
                    url,
                    item.get("title"),
                    item.get("price"),
                    item.get("sqm"),
                    item.get("rooms"),
                    item.get("address"),
                    item.get("image_url"),
                    str(item.get("description") or ""),
                    raw_json,
                ),
            )
        await db.commit()


async def fetch_recent_listings_for_dedup(
    *,
    exclude_source: str,
    days: int = 7,
    db: aiosqlite.Connection | None = None,
) -> list[dict[str, Any]]:
    """Объявления из listings за последние N дней, кроме указанной площадки."""
    query = """
        SELECT source, external_id, price, size_sqm, rooms, location
        FROM listings
        WHERE source != ?
          AND datetime(COALESCE(updated_at, created_at)) >= datetime('now', ?)
    """
    params = (exclude_source, f"-{int(days)} days")

    if db is not None:
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async with _connect() as connection:
        async with connection.execute(query, params) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def is_apartment_seen(user_id: int, apt_id: str) -> bool:
    """Показывали ли уже это объявление пользователю."""
    async with _connect() as db:
        async with db.execute(
            "SELECT 1 FROM seen_apartments WHERE user_id = ? AND apartment_id = ?",
            (user_id, apt_id),
        ) as cursor:
            return await cursor.fetchone() is not None


async def mark_apartment_seen(
    user_id: int, apt_id: str, *, was_match: bool = False
) -> None:
    """Помечает объявление как проверенное. Совпадение не затирается отказом."""
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO seen_apartments (user_id, apartment_id, was_match)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, apartment_id) DO UPDATE SET
                was_match = MAX(seen_apartments.was_match, excluded.was_match)
            """,
            (user_id, apt_id, int(bool(was_match))),
        )
        await db.commit()


def _utc_day() -> str:
    """Текущая дата в UTC в виде YYYY-MM-DD."""
    return datetime.now(tz=UTC).strftime("%Y-%m-%d")


async def count_ai_calls_today(user_id: int) -> int:
    """Сколько раз пользователь обращался к OpenAI за сегодня."""
    async with _connect() as db:
        async with db.execute(
            "SELECT calls FROM ai_usage WHERE user_id = ? AND day = ?",
            (user_id, _utc_day()),
        ) as cursor:
            row = await cursor.fetchone()
    return int(row["calls"]) if row else 0


async def count_anschreiben_today(user_id: int) -> int:
    """Anschreiben, отправленные пользователю сегодня (UTC)."""
    day = _utc_day()
    async with _connect() as db:
        async with db.execute(
            """
            SELECT COUNT(*) AS cnt FROM seen_apartments
            WHERE user_id = ? AND was_match = 1
              AND strftime('%Y-%m-%d', created_at) = ?
            """,
            (user_id, day),
        ) as cursor:
            row = await cursor.fetchone()
    return int(row["cnt"]) if row else 0


async def count_users_total() -> int:
    async with _connect() as db:
        async with db.execute("SELECT COUNT(*) AS cnt FROM users") as cursor:
            row = await cursor.fetchone()
    return int(row["cnt"]) if row else 0


async def count_auto_search_active() -> int:
    async with _connect() as db:
        async with db.execute(
            """
            SELECT COUNT(*) AS cnt FROM users
            WHERE is_active = 1
              AND COALESCE(is_auto_search_enabled, 1) = 1
              AND city IS NOT NULL AND TRIM(city) != ''
            """
        ) as cursor:
            row = await cursor.fetchone()
    return int(row["cnt"]) if row else 0


async def count_listings_total() -> int:
    async with _connect() as db:
        async with db.execute("SELECT COUNT(*) AS cnt FROM listings") as cursor:
            row = await cursor.fetchone()
    return int(row["cnt"]) if row else 0


async def count_anschreiben_total() -> int:
    async with _connect() as db:
        async with db.execute(
            "SELECT COUNT(*) AS cnt FROM seen_apartments WHERE was_match = 1"
        ) as cursor:
            row = await cursor.fetchone()
    return int(row["cnt"]) if row else 0


async def count_anschreiben_today_all() -> int:
    day = _utc_day()
    async with _connect() as db:
        async with db.execute(
            """
            SELECT COUNT(*) AS cnt FROM seen_apartments
            WHERE was_match = 1 AND strftime('%Y-%m-%d', created_at) = ?
            """,
            (day,),
        ) as cursor:
            row = await cursor.fetchone()
    return int(row["cnt"]) if row else 0


async def register_ai_call(user_id: int) -> int:
    """Увеличивает счётчик обращений и возвращает новое значение."""
    day = _utc_day()
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO ai_usage (user_id, day, calls) VALUES (?, ?, 1)
            ON CONFLICT(user_id, day) DO UPDATE SET calls = calls + 1
            """,
            (user_id, day),
        )
        await db.commit()
        async with db.execute(
            "SELECT calls FROM ai_usage WHERE user_id = ? AND day = ?", (user_id, day)
        ) as cursor:
            row = await cursor.fetchone()
    return int(row["calls"]) if row else 0


def _to_int_flag(value: Any) -> int | None:
    """Преобразует bool-подобное значение в 0/1 для хранения в SQLite."""
    return None if value is None else int(bool(value))


def _to_optional_sqm_max(value: Any) -> float | None:
    """NULL или 0 — «без верхней границы», остальное оставляем числом."""
    if value is None:
        return None
    number = float(value)
    return None if number <= 0 else number


def _to_optional_income(value: Any) -> int | None:
    """Чистый доход: целое число евро или NULL, если поле пропущено."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
