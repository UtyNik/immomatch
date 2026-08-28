"""Конфигурация приложения: читается из переменных окружения / файла .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR: Path = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Настройки бота ImmoMatch AI."""

    bot_token: SecretStr
    openai_api_key: SecretStr
    # Относительный путь считается от корня проекта. В Docker compose
    # задаётся абсолютный /app/data/immomatch.db и монтируется том ./data.
    db_path: Path = Path("data/immomatch.db")
    # Потолок обращений к OpenAI на пользователя в сутки. 0 — без ограничения
    # (удобно на время тестов).
    ai_daily_limit: int = 20
    # Как часто фоновый автопоиск снова обходит kleinanzeigen.de.
    auto_search_interval_minutes: int = 10
    # Сколько пользователей автопоиск обрабатывает одновременно (asyncio, не потоки).
    auto_search_concurrency: int = 3
    # Telegram user id администратора для алертов парсеров (опционально).
    admin_telegram_id: int | None = None

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def ai_budget_left(self, used: int) -> bool:
        """Есть ли ещё попытки на сегодня. Ноль в настройке — лимита нет."""
        return self.ai_daily_limit <= 0 or used < self.ai_daily_limit

    @property
    def db_file(self) -> Path:
        """Абсолютный путь к файлу SQLite (относительные пути — от корня проекта)."""
        path = self.db_path
        return path if path.is_absolute() else BASE_DIR / path

    def prepare_storage(self) -> None:
        """Создаёт каталог для базы данных, если его ещё нет."""
        self.db_file.parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Возвращает singleton-конфиг: файл .env читается только один раз."""
    return Settings()  # type: ignore[call-arg]  # значения приходят из окружения
