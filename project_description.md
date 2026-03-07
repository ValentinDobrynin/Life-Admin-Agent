# Life Admin Agent — Project Description

## Что строим

Персональный операционный диспетчер для бытовых и полуличных задач.
Агент хранит важные объекты (документы, поездки, подарки, сертификаты, подписки, счета),
знает их сроки, напоминает заранее и подсказывает следующий шаг.

Главная ценность — снижение фоновой когнитивной нагрузки.
Не "умный чатик", а операционный слой над личной жизнью.

## Для кого

Один пользователь + семья. Один аккаунт, никакой мультиюзерности.
Продукт для себя, не SaaS.

## Стек

| Слой | Технология |
|------|-----------|
| Backend | Python 3.11+, FastAPI |
| База данных | PostgreSQL (Render managed) |
| ORM / миграции | SQLAlchemy + Alembic |
| Планировщик | APScheduler (внутри FastAPI, не отдельный сервис) |
| Telegram | aiogram (webhook, не polling) |
| Email inbound | Mailgun inbound parsing → POST на /webhook/email |
| LLM | OpenAI API (gpt-4o) |
| Файлы | Cloudflare R2 (boto3 с кастомным endpoint_url) |
| Google Calendar | google-api-python-client (перенесён из calendar_bot) |
| Деплой | Render Web Service |

## Архитектурные принципы

**1. Никакой бизнес-логики в роутерах**
Роутеры (`bot/handlers.py`, `api/routes.py`) только принимают запрос и вызывают модуль.
Вся логика — в `modules/`. Роутер не знает, что такое entity или reminder.

**2. Все внешние вызовы через отдельный модуль**
OpenAI → только через `modules/parser.py` и `modules/suggestions.py`.
Telegram → только через `modules/notifications.py`.
Google Calendar → только через `modules/google_calendar.py`.
Cloudflare R2 → только через `modules/storage.py`.
Прямых вызовов внешних API из handlers или scheduler — нет.

**3. Никогда не удалять, только архивировать**
Любой объект в БД может быть archived или paused, но не удалён.
История в event_log — неприкосновенна.

**4. Digest first, push only for urgent**
Нессрочные напоминания идут в утренний дайджест.
Точечный пуш — только если объект истекает в течение 48 часов или помечен как срочный.

**5. Каждое сообщение пользователю — action-oriented**
Любое уведомление заканчивается inline-кнопками или явным next_action.
Информация без действия — не отправляется.

**6. Модули изолированы**
Каждый модуль импортирует только то, что ему нужно из `models.py` и других модулей.
Циклических зависимостей нет.
Порядок зависимостей: models → modules → bot/api → scheduler.

## Структура проекта

```
life-admin-agent/
├── main.py                  # FastAPI app + lifespan (запуск scheduler)
├── config.py                # Pydantic Settings, все env vars
├── database.py              # SQLAlchemy engine + get_db dependency
├── models.py                # Все 6 таблиц: entities, reminders, checklist_items,
│                            #   contacts, resources, event_log
├── scheduler.py             # APScheduler: check_reminders, send_digest, lifecycle_check
├── bot/
│   ├── handlers.py          # Telegram webhook handlers (только роутинг)
│   ├── client.py            # Telegram Bot API client
│   └── email_handler.py     # Mailgun inbound webhook handler
├── modules/
│   ├── ingestion.py         # Приём raw input, нормализация, сохранение raw_record
│   ├── parser.py            # OpenAI: извлечение entity из raw input
│   ├── reminders.py         # Создание и управление reminder rules
│   ├── suggestions.py       # next_action, shortlist, inline-кнопки
│   ├── notifications.py     # send_telegram(), send_digest() — единственный выход в TG
│   ├── storage.py           # Cloudflare R2: upload, get_url
│   ├── lifecycle.py         # Автоархив, monthly review
│   ├── google_auth.py       # OAuth credentials (перенесён из calendar_bot)
│   └── google_calendar.py   # Google Calendar CRUD (перенесён из calendar_bot)
├── api/
│   ├── routes.py            # /api/* заглушки (501 Not Implemented) — для будущего Web UI
│   └── auth.py              # verify_token dependency
├── prompts/
│   ├── entity_parser.txt    # Системный промпт для Entity Parser
│   └── suggestion.txt       # Промпт для Suggestion Engine
└── migrations/              # Alembic
```

## Модель данных (кратко)

- **entities** — любой объект реального мира (поездка, полис, сертификат, подарок...)
- **reminders** — напоминания по entity с правилами (before_N_days, on_date, digest_only...)
- **checklist_items** — шаги внутри объекта, с зависимостями
- **contacts** — люди: именинники, контакты поездок, агенты
- **resources** — файлы и ссылки, привязанные к entity (r2_key или url)
- **event_log** — история всех действий. Нужна чтобы не напоминать бесконечно об одном

## Категории объектов

`document` · `trip` · `gift` · `certificate` · `subscription` · `payment` · `logistics`

## Источники данных (MVP)

- Ручной ввод через Telegram
- Фото и PDF через Telegram (парсинг через OpenAI Vision)
- Пересылка email на Mailgun-адрес
- Google Calendar (заглушка на чтение)

## Что явно НЕ делаем (anti-goals)

- **Нет автоматического чтения почты** — только пересылка руками. Privacy и хаос.
- **Нет polling** — только Telegram webhook.
- **Нет отдельного Background Worker на Render** — Scheduler живёт внутри Web Service.
- **Нет мультиюзерности** — один аккаунт, без ролей и permissions.
- **Нет автоплатежей** — агент напоминает и даёт ссылку, не платит сам.
- **Нет браузерного агента** — не выполняет действия в интернете от имени пользователя.
- **Нет удаления объектов** — только archive/paused статусы.
- **Нет бизнес-логики в роутерах** — роутеры только принимают и делегируют.
- **Web UI не в MVP** — только заглушки /api/* (501). Не реализовывать раньше времени.

## Переменные окружения

```
DATABASE_URL=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
OPENAI_API_KEY=
MAILGUN_API_KEY=
MAILGUN_DOMAIN=
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REFRESH_TOKEN=
API_KEY=                     # статический токен для /api/* заглушек
```

## Timezone

Europe/Moscow — дефолт для всех дат и напоминаний.
Хранить в БД всегда в UTC, конвертировать при отображении.

## Язык

Все сообщения пользователю — на русском.
Код, комментарии, имена переменных — на английском.
Промпты для OpenAI — на русском (модель работает лучше на том языке, на котором будет отвечать).
