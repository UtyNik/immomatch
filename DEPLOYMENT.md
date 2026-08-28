# Развёртывание ImmoMatch AI на Linux VPS

Бот работает в Docker и ходит в Telegram через **long polling**: открывать порты на сервере не нужно. Нужен исходящий HTTPS к `api.telegram.org`, `api.openai.com` и `www.kleinanzeigen.de`.

SQLite лежит в каталоге `./data` на хосте и монтируется в контейнер как `/app/data`. Файл базы: `immomatch.db`.

## 1. Требования

- VPS с Ubuntu **22.04** или **24.04** (Debian 12 — в конце раздела про Docker).
- Права `sudo`.
- Токен бота от [@BotFather](https://t.me/BotFather).
- Ключ OpenAI API.

Минимум: 1 vCPU, 1 ГБ RAM, 10 ГБ диска.

## 2. Docker и Docker Compose (Ubuntu 22.04 / 24.04)

Официальный репозиторий Docker, пакет `docker-compose-plugin` (команда `docker compose`, без дефиса).

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl

sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Проверка:

```bash
sudo docker run --rm hello-world
docker compose version
```

Чтобы вызывать Docker без `sudo` (после этого **перелогиньтесь**):

```bash
sudo usermod -aG docker "$USER"
```

Сервис Docker включится в автозагрузку сам. Контейнер с `restart: always` поднимется после ребута VPS.

### Debian 12

Те же шаги, только репозиторий `debian` вместо `ubuntu`:

```bash
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

## 3. Код на сервере

### Вариант A: git

```bash
sudo apt-get install -y git
cd /opt
sudo git clone <URL_РЕПОЗИТОРИЯ> immomatch_bot
sudo chown -R "$USER":"$USER" /opt/immomatch_bot
cd /opt/immomatch_bot
```

### Вариант B: архив (scp / sftp)

С своей машины:

```bash
scp -r ./immomatch_bot user@YOUR_SERVER_IP:/opt/immomatch_bot
```

На сервере:

```bash
cd /opt/immomatch_bot
```

В каталоге должны быть `Dockerfile`, `docker-compose.yml`, `bot.py`, `requirements.txt`.

## 4. Файл `.env`

Секреты **не** кладутся в образ. Compose читает `.env` с хоста.

```bash
cp .env.example .env
nano .env
```

Обязательно заполните:

```env
BOT_TOKEN=123456:ABC...
OPENAI_API_KEY=sk-...
```

Остальное можно оставить как в шаблоне. Путь к БД в контейнере задаёт `docker-compose.yml` (`DB_PATH=/app/data/immomatch.db`) — строка `DB_PATH` в `.env` на это не влияет.

Права:

```bash
chmod 600 .env
```

Не коммитьте `.env` в git.

## 5. Каталог данных

Контейнер работает от пользователя с uid **1000**. Каталог `./data` на хосте должен ему принадлежать, иначе SQLite не создастся.

```bash
mkdir -p data
sudo chown 1000:1000 data
```

Если раньше база была `data/bot.db`, один раз скопируйте её:

```bash
sudo cp data/bot.db data/immomatch.db
sudo chown 1000:1000 data/immomatch.db
```

## 6. Сборка и запуск

```bash
cd /opt/immomatch_bot
docker compose up -d --build
```

`-d` — в фоне, `--build` — пересобрать образ.

Статус:

```bash
docker compose ps
```

Состояние `Up` и `restart: always` — контейнер поднимется после падения процесса и после перезагрузки сервера.

## 7. Логи

Все логи вслед за контейнером:

```bash
docker compose logs -f
```

Последние 200 строк:

```bash
docker compose logs --tail=200
```

В логе при старте должна быть строка вида: бот запущен и путь к БД `/app/data/immomatch.db`.

Остановка слежения: `Ctrl+C` (контейнер продолжает работать).

## 8. Типовые команды

```bash
# остановить
docker compose stop

# запустить снова (без пересборки)
docker compose start

# пересобрать и перезапустить после git pull
git pull
docker compose up -d --build

# полный стоп с удалением контейнера (том ./data не трогается)
docker compose down
```

База в `./data/immomatch.db` после `down` остаётся.

## 9. Бэкап SQLite

Контейнер можно не останавливать, но надёжнее короткая пауза:

```bash
docker compose stop
cp data/immomatch.db "/root/backups/immomatch-$(date +%F).db"
docker compose start
```

Или снимок на лету:

```bash
sqlite3 data/immomatch.db ".backup '/root/backups/immomatch-$(date +%F).db'"
```

Пакет: `sudo apt-get install -y sqlite3`.

## 10. Если бот не стартует

| Симптом | Что проверить |
| --- | --- |
| `в .env не найдены переменные: BOT_TOKEN` | Файл `.env` в том же каталоге, что `docker-compose.yml`; строки вида `ИМЯ=значение` без кавычек |
| `Telegram отклонил токен` | Актуальный токен у @BotFather |
| `unable to open database file` | `sudo chown 1000:1000 data` |
| `Conflict: terminated by other getUpdates` | Второй экземпляр бота (ещё один контейнер или `python bot.py` на той же машине / другом VPS) |
| Сайт Kleinanzeigen 403 | Исходящий IP VPS заблокирован; подождать или сменить IP |

Пересобрать с нуля (база на хосте сохранится):

```bash
docker compose down
docker compose up -d --build
```
