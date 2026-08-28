# ImmoMatch AI

Мультиплатформенный Telegram-бот для поиска жилья в Германии. Собирает объявления с **Kleinanzeigen**, **Immowelt** и **WG-Gesucht**, фильтрует по анкете пользователя, оценивает совпадение через OpenAI и генерирует готовое немецкое **Anschreiben**.

Интерфейс на трёх языках: украинский, русский, английский.

## Возможности

- Анкета с бюджетом (Warmmiete), комнатами, площадью, WBS/Jobcenter, питомцами и др.
- Параллельный поиск на трёх площадках через `SearchOrchestrator`.
- Дешёвый предфильтр (цена Kalt/Warm, комнаты, площадь) до обращения к AI.
- Кросс-платформенная дедупликация — одно и то же жильё с разных сайтов не показывается дважды.
- AI-оценка и Anschreiben (`gpt-4o-mini`), с обращением по имени контакта на Kleinanzeigen.
- Фоновый автопоиск с push-уведомлениями.
- Алерты администратору в Telegram при CAPTCHA, 403, 429 и сбоях парсинга.

## Архитектура

```
Пользователь (Telegram)
        │
        ▼
   handlers/search.py  ──►  SearchOrchestrator
        │                         │
        │            ┌────────────┼────────────┐
        │            ▼            ▼            ▼
        │     Kleinanzeigen  Immowelt    WG-Gesucht
        │       Provider      Provider      Provider
        │            └────────────┬────────────┘
        │                         ▼
        │                  BaseProvider / ListingData
        │
        ├──► deduplicator.py  (таблица listings, 7 дней)
        ├──► ai_agent.py      (оценка + Anschreiben)
        └──► database/db.py   (SQLite: users, seen_apartments, listings, ai_usage)
```

### SearchOrchestrator

`services/search_orchestrator.py` параллельно опрашивает все активные провайдеры (`asyncio.gather`). Сбой одной площадки не блокирует остальные. Результат — единый список legacy-словарей с ключом `storage_id` вида `{source}:{external_id}`.

### Провайдеры (BaseProvider)

Каждая площадка реализует `BaseProvider`:

| Провайдер | Модуль | Особенности |
|-----------|--------|-------------|
| Kleinanzeigen | `services/parsers/kleinanzeigen.py` | HTML-карточки, Warmmiete со страницы объявления |
| Immowelt | `services/parsers/immowelt.py` | JSON `__NEXT_DATA__` / HTML, expose-страницы |
| WG-Gesucht | `services/parsers/wggesucht.py` | HTML + fallback через sitemap и `/api/offers/{id}` |

Общая DTO — `ListingData` в `services/parsers/base.py`.

### Дедупликация

`services/deduplicator.py` сравнивает новое объявление с записями в таблице `listings` за последние 7 дней. Совпадение: тот же город (нормализация с PLZ), комнаты, площадь ±1 m², цена ±15 €, **другой** `source`. Дубликаты помечаются как `seen` без вызова OpenAI.

### SQLite

База по умолчанию: `data/immomatch.db`. Основные таблицы: `users`, `seen_apartments`, `listings`, `ai_usage`. Схема мигрируется при старте бота (`ALTER TABLE`).

### Алерты администратору

`services/alerts.py` отправляет сообщения на `ADMIN_TELEGRAM_ID` при CAPTCHA, HTTP 403/429 и фатальных ошибках парсинга. Cooldown — **1 час** на пару (площадка, тип проблемы), чтобы не спамить.

### Вежливый парсинг

`services/http_politeness.py` — случайная пауза `1.5–3.5 с` перед HTTP-запросами в провайдерах, эвристика `detect_block_reason()` для CAPTCHA / Cloudflare / DataDome.

## Структура проекта

```
immomatch_bot/
├── bot.py                      # точка входа
├── config.py                   # настройки из .env (pydantic-settings)
├── texts.py                    # тексты UI (ua/ru/en)
├── validators.py               # разбор чисел, Kalt/Warm
├── database/db.py              # SQLite
├── handlers/                   # onboarding, search, common
├── services/
│   ├── search_orchestrator.py
│   ├── parsers/                # base, kleinanzeigen, immowelt, wggesucht
│   ├── deduplicator.py
│   ├── ai_agent.py
│   ├── alerts.py
│   ├── http_politeness.py
│   ├── scheduler.py
│   └── listing_price.py
├── scrapers/kleinanzeigen.py   # низкоуровневый парсер Kleinanzeigen
├── test_wggesucht.py           # локальный тест WG-Gesucht
├── test_dedup.py               # локальный тест дедупликации
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## Переменные окружения (`.env`)

Скопируй `.env.example` в `.env` и заполни значения.

| Переменная | Обязательна | Описание |
|------------|-------------|----------|
| `BOT_TOKEN` | да | Токен Telegram-бота от @BotFather |
| `OPENAI_API_KEY` | да | Ключ OpenAI API |
| `DB_PATH` | нет | Путь к SQLite (по умолчанию `data/immomatch.db`) |
| `AI_DAILY_LIMIT` | нет | Лимит обращений к OpenAI на пользователя в сутки; `0` — без лимита (по умолчанию `20`) |
| `AUTO_SEARCH_INTERVAL_MINUTES` | нет | Интервал фонового автопоиска в минутах (по умолчанию `10`) |
| `AUTO_SEARCH_CONCURRENCY` | нет | Сколько пользователей автопоиск обрабатывает параллельно (по умолчанию `3`) |
| `ADMIN_TELEGRAM_ID` | нет | Telegram user id администратора для алертов парсеров (узнать: @userinfobot) |

Пример:

```env
BOT_TOKEN=123456:ABC...
OPENAI_API_KEY=sk-...
DB_PATH=data/immomatch.db
AI_DAILY_LIMIT=50
AUTO_SEARCH_INTERVAL_MINUTES=15
AUTO_SEARCH_CONCURRENCY=3
ADMIN_TELEGRAM_ID=654428007
```

## Локальная разработка

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
# отредактируй .env
python bot.py
```

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

### Локальные тесты

Проверка парсера WG-Gesucht (город и бюджет — аргументы CLI):

```bash
python test_wggesucht.py "Freiburg im Breisgau" 1200
```

Проверка дедупликации (in-memory SQLite, без сети):

```bash
python test_dedup.py
```

Проверка синтаксиса всего проекта:

```bash
python -m compileall -q .
```

## Docker

### Первый запуск

```bash
cp .env.example .env
# заполни .env на сервере

docker compose build
docker compose up -d
```

### Обновление после изменений в коде

```bash
git pull
docker compose build --no-cache
docker compose up -d
```

### Логи

```bash
docker compose logs -f immomatch-bot
```

Последние 200 строк:

```bash
docker compose logs --tail=200 immomatch-bot
```

### Остановка и очистка

```bash
docker compose down
docker image prune -f
```

База SQLite хранится на хосте в `./data` и переживает пересборку контейнера.

Подробнее о деплое на VPS: [DEPLOYMENT.md](DEPLOYMENT.md).

## Сценарий поиска (кратко)

1. Проверка суточного лимита AI.
2. `SearchOrchestrator` параллельно запрашивает Kleinanzeigen, Immowelt, WG-Gesucht.
3. Отсечение уже показанных (`seen_apartments`) и дешёвый фильтр по бюджету/комнатам/площади.
4. `load_details` — догрузка страниц объявлений (Warmmiete, описание).
5. Дедупликация по `listings`.
6. AI оценивает кандидатов по одному; перебор останавливается на первом совпадении.
7. Карточка с ценой Kalt/Warm, бейджем площадки, Anschreiben и кнопками «Перейти» / «Искать дальше».

Фоновый автопоиск (`services/scheduler.py`) повторяет тот же pipeline для пользователей с включённым автопоиском.

## Эксплуатация

- Бот использует порт `127.0.0.1:47653` как lock — второй процесс не стартует (защита от `TelegramConflictError`).
- Состояния анкеты в `MemoryStorage` — при перезапуске незавершённый диалог сбрасывается.
- Парсеры зависят от вёрстки чужих сайтов; при блокировках админ получает алерт (если задан `ADMIN_TELEGRAM_ID`).
- Не коммить `.env` с реальными токенами.

## Добавление хэндлеров

Создай модуль в `handlers/` с `router = Router(name="...")` и добавь его в `get_routers()` в `handlers/__init__.py`. Порядок роутеров в списке важен.
