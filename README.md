# Telegram-бот для мастера маникюра Екатерины (aiogram 3.x)

Production-ready шаблон бота для бьюти-сферы:
- роли (клиент/админ),
- запись и календарь,
- предоплата через ЮKassa,
- бонусы и рефералы,
- отзывы и портфолио,
- админка в Telegram,
- мультиязычность (RU/EN),
- антифлуд, логи, ежедневный backup БД,
- long polling (Telegram),
- Docker-деплой.

## Структура

```text
tgbot/
  app/
    __init__.py
    config.py
    db.py
    handlers.py
    i18n.py
    keyboards.py
    logging_setup.py
    main.py
    middlewares.py
    models.py
    payment.py
    scheduler.py
    services.py
    states.py
  .env.example
  Dockerfile
  docker-compose.yml
  requirements.txt
  README.md
```

## Функционал

### 1) Авторизация и роли
- Регистрация при `/start` с записью в БД.
- Автоопределение языка по Telegram (`ru`/`en`).
- Роли:
  - `client` (по умолчанию),
  - `admin` (если `telegram_id` есть в `ADMINS`).
- Поддержка нескольких админов через список `ADMINS=id1,id2,...`.

### 2) Запись на услуги
- Интерактивный выбор услуги (inline кнопки).
- Дата и время через FSM.
- Проверка пересечения записей.
- Отмена записи:
  - запрещена менее чем за 3 часа,
  - возврат предоплаты разрешен при отмене за 24+ часа.
- Напоминания в фоне:
  - за 24 часа,
  - за 2 часа.

### 3) Онлайн-оплата
- Интеграция с ЮKassa API.
- Предоплата `PREPAYMENT_PERCENT` (по умолчанию 30%).
- Автоподтверждение записи по платежному webhook.
- Заготовка под возврат через `refund_payment`.

### 4) Портфолио и отзывы
- Портфолио хранится в БД (`photo_file_id`, `caption`) и отправляется как карусель сообщений.
- Отзывы: оценка 1..5 + текст.
- Автопубликация новых отзывов в канал/чат (`REVIEW_CHANNEL_ID`).

### 5) Админ-панель
- Просмотр статистики: кол-во записей, выручка, популярная услуга.
- Просмотр записей на день/неделю/месяц.
- Добавление услуг.
- Завершение записи с отправкой чека клиенту.
- Рассылка всем клиентам (текст).

### 6) Бонусы и рефералы
- Начисление бонусов после оплаты (`BONUS_PERCENT`, по умолчанию 5%).
- Реферальная ссылка вида `/start ref_<telegram_id>`.
- При первом входе по ссылке оба получают `REFERRAL_BONUS`.

### 7) Дополнительно
- Чек после оказания услуги (текстовый чек в Telegram).
- Логирование действий в `logs/bot.log`.
- Anti-flood middleware (`MAX_REQUESTS_PER_MINUTE`).
- Эмодзи + inline-кнопки во всех ключевых сценариях.

### 8) Техчасть
- FSM: запись/отзывы/добавление услуг/рассылка.
- Конфиги в `.env`.
- Dockerfile + docker-compose.
- Telegram работает через long polling (без публичного URL).
- Backup SQLite раз в сутки (03:00, timezone из `TIMEZONE`).

## Быстрый запуск (локально)

1. Создайте виртуальное окружение и установите зависимости:

```bash
python -m venv .venv
. .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate  # Windows PowerShell
pip install -r requirements.txt
```

2. Подготовьте env:

```bash
cp .env.example .env
```

3. Заполните минимум:
- `BOT_TOKEN`
- `ADMINS`
- `YOOKASSA_SHOP_ID`
- `YOOKASSA_SECRET_KEY`
- `REVIEW_CHANNEL_ID` (опционально)

4. Запуск:

```bash
python -m app.main
```

## Запуск в Docker

```bash
docker compose up -d --build
```

## Режим запуска

Бот запущен в `long polling`, поэтому:
- `ngrok` и публичный домен не требуются;
- бот работает, пока ваш ПК включен и процесс `python -m app.main` активен.

Важно: webhook платежей из ЮKassa в таком режиме не придет на локальный ПК без публичного URL.
Для боевого приема онлайн-оплаты переключайтесь на webhook-режим (домен/VPS/туннель).

## База данных

- По умолчанию: SQLite (`sqlite+aiosqlite:///./data/bot.db`).
- Для PostgreSQL:
  - установите `DB_URL`, например:
    `postgresql+asyncpg://user:pass@host:5432/dbname`.

## Windows: ошибки SSL к `api.telegram.org`

Если видите `SSL: INVALID_SESSION_ID` или `ClientConnectorSSLError`:

1. `pip install -r requirements.txt` (нужен пакет `truststore`).
2. В `.env` по очереди попробуйте:
   - `TELEGRAM_FORCE_TLS12=true`
   - `TELEGRAM_IPV4_ONLY=true`
   - крайний случай: `TELEGRAM_INSECURE_SSL=true` (без проверки сертификата)
3. Отключите в антивирусе «сканирование HTTPS» или добавьте исключение.
4. Проверьте сеть с телефона (раздать интернет), чтобы исключить блокировку провайдера.
5. Если рукопожатие «висит» ~60 секунд или `Cannot connect ... [None]` — часто **блокируется доступ к Bot API**. Включите VPN и укажите локальный прокси в `.env`, например:
   - `TELEGRAM_PROXY=http://127.0.0.1:7890` (типичный HTTP-прокси Clash/V2RayN)
   - или `TELEGRAM_PROXY=socks5://127.0.0.1:10808` (зависит от вашего VPN-клиента; для SOCKS нужен пакет `aiohttp-socks`, он в `requirements.txt`)

## Что стоит добавить для полного enterprise

- Redis для FSM/ratelimit в кластере.
- Celery/RQ для надежных фоновых задач.
- Миграции Alembic.
- Подпись/проверка платежных webhook.
- Полноценный PDF-чек через онлайн-кассу (54-ФЗ) при необходимости.
 Telegram Dark Client

Open-source веб-клиент для Telegram Bot API в темном стиле:
- темная тема и закругленные кнопки;
- легкий и быстрый (без внешних зависимостей);
- отправка сообщений и получение обновлений;
- хранение настроек локально в браузере.

## Быстрый старт

1. Создайте бота через [@BotFather](https://t.me/BotFather) и получите `BOT_TOKEN`.
2. Узнайте `chat_id` (например, через [@userinfobot](https://t.me/userinfobot) или через `getUpdates`).
3. Откройте `index.html` в браузере.
4. Введите `BOT_TOKEN` и `chat_id`, нажмите "Подключить".

## Как это работает

- Подключение проверяется через `getMe`.
- Сообщения отправляются через `sendMessage`.
- Входящие обновления подтягиваются через `getUpdates`.

## Ограничения

- Это клиент **Bot API**, а не полноценный MTProto-клиент пользовательского аккаунта.
- Токен хранится в `localStorage` в рамках вашего браузера.
- Для продакшена рекомендуется проксировать запросы через ваш backend.

## Лицензия

MIT
