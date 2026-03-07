# Life Admin Agent — Backlog

> Единственный канонический список задач проекта.
> Порядок разделов = порядок реализации.
> Тесты пишутся после того как срез работает end-to-end, но до начала следующего среза.

---

## Архитектурные решения (зафиксировано)

| Вопрос | Решение |
|--------|---------|
| Telegram | aiogram, webhook (не polling) |
| Scheduler | APScheduler внутри FastAPI Web Service |
| Render tier | Starter (не засыпает) |
| Email inbound | Заглушка в MVP, Mailgun в backlog |
| OpenAI Vision | Не оптимизируем пока |
| owner_id | Поле есть (задел на v2), логика одного пользователя |
| Промпты | Всегда в `prompts/*.txt`, никогда в Python |
| Порядок сборки | Вертикальные срезы |
| Тесты | После среза end-to-end, до следующего среза |
| Паттерны тестов | Из `calendar_bot/tests/` (mock, conftest, pytest-asyncio) |

---

## 🔴 High Priority

### [TECH-001] Инициализация проекта

**Статус:** ✅ Done
**Приоритет:** High
**Компонент:** Infrastructure

**Описание проблемы**
Проект пустой. Нужна базовая структура, зависимости, инструменты проверки и CI.

**Ожидаемое поведение**
После выполнения: `make check` проходит, структура директорий создана, `.env.example` описывает все переменные.

**Технические детали**
- `pyproject.toml` с ruff, mypy, pytest (по образцу calendar_bot, адаптировать под структуру без вложенного пакета)
- `Makefile` с командами: `check`, `format`, `lint`, `types`, `test`, `run`
- `requirements.txt` + `requirements-dev.txt`
- `.env.example` со всеми переменными окружения
- `render.yaml` для Render Web Service (Starter plan)
- Структура директорий: `bot/`, `modules/`, `api/`, `prompts/`, `migrations/`, `tests/`
- `tests/conftest.py` по образцу calendar_bot (env vars, базовые фикстуры)
- `.gitignore` (Python + .env)
- GitHub Actions CI: `make check` на push/PR

**Критерии приемки**
- [x] `pyproject.toml` создан, ruff и mypy настроены
- [x] `Makefile` с `make check` запускает lint + types + tests
- [x] `requirements.txt` содержит все prod-зависимости
- [x] `requirements-dev.txt` содержит ruff, mypy, pytest, pytest-asyncio, httpx, pytest-mock
- [x] `.env.example` содержит все 14 переменных окружения
- [x] Структура директорий создана (пустые `__init__.py`)
- [x] `tests/conftest.py` создан с env var defaults
- [x] `render.yaml` создан
- [x] GitHub Actions CI создан
- [x] `make check` проходит на чистом проекте

**Решение**
- Создана полная структура: `bot/`, `modules/`, `api/`, `prompts/`, `migrations/`, `tests/`
- `pyproject.toml` с ruff (line-length=100, select E/F/I/UP) и mypy
- `Makefile`: `check` = lint + types + test; отдельно `format`, `run`, `migrate`
- Python 3.11 venv; все зависимости установлены
- `render.yaml` для Starter plan с `alembic upgrade head` в buildCommand
- GitHub Actions CI в `.github/workflows/ci.yml`
- `tests/conftest.py` с дефолтами для всех env vars (паттерн из calendar_bot)

---

### [TECH-002] Конфигурация, база данных, модели

**Статус:** ✅ Done
**Приоритет:** High
**Компонент:** Infrastructure / Data

**Описание проблемы**
Нужны базовые слои: конфигурация через Pydantic Settings, SQLAlchemy engine, и все 6 таблиц с миграциями Alembic.

**Ожидаемое поведение**
`alembic upgrade head` создаёт все таблицы. `config.py` читает env vars и валидирует их.

**Технические детали**

`config.py`:
- Pydantic `BaseSettings`, все env vars, computed field `is_production`
- Timezone default: `Europe/Moscow`

`database.py`:
- SQLAlchemy async engine (`asyncpg`)
- `AsyncSessionLocal`, `get_db` dependency для FastAPI

`models.py` — 6 таблиц:
- `entities`: `id, type, name, start_date, end_date, status, priority, owner_id, notes, created_at, updated_at`
  - `type` ∈ `document | trip | gift | certificate | subscription | payment | logistics`
  - `status` ∈ `active | expiring_soon | expired | archived | paused | closed`
- `reminders`: `id, entity_id (FK), trigger_date, rule, channel, text, status, snoozed_until`
  - `rule` ∈ `before_N_days | on_date | recurring_weekly | digest_only`
  - `status` ∈ `pending | sent | snoozed | cancelled`
- `checklist_items`: `id, entity_id (FK), text, due_date, status, depends_on (FK self), position`
- `contacts`: `id, name, birthday, notes, gift_history (JSON), entity_ids (JSON), created_at`
- `resources`: `id, entity_id (FK), type, url, r2_key, filename, notes`
  - `type` ∈ `file | link | contact`
- `event_log`: `id, entity_id (FK nullable), action, payload (JSON), created_at`

`migrations/`:
- Alembic init, первая миграция создаёт все таблицы

**Критерии приемки**
- [x] `config.py` читает все env vars, валидирует типы
- [x] `database.py` с async engine и `get_db` dependency
- [x] `models.py` содержит все 6 таблиц с правильными полями и FK
- [x] `alembic upgrade head` применяется без ошибок
- [x] Тесты: `test_config.py` — валидация обязательных полей, дефолты
- [x] Тесты: `test_models.py` — создание объектов, FK constraints (SQLite in-memory)
- [x] `make check` проходит

**Решение**
- `config.py`: Pydantic `BaseSettings`, 17 полей, `is_production` computed property
- `database.py`: `create_async_engine` + `async_sessionmaker` + `get_db` dependency
- `models.py`: 6 таблиц (Entity, Reminder, ChecklistItem, Contact, Resource, EventLog) с relationships и cascade
- `migrations/env.py` адаптирован для async SQLAlchemy (asyncio.run + async_engine_from_config)
- Первая миграция `initial_schema` сгенерирована через autogenerate
- 11 тестов: 4 config + 7 models, все через SQLite in-memory

---

### [FEATURE-001] Срез 1: Telegram Capture → БД

**Статус:** ✅ Done
**Приоритет:** High
**Компонент:** Bot / Ingestion / Parser

**Описание проблемы**
Первый вертикальный срез: пользователь пишет в Telegram → агент парсит через OpenAI → сохраняет entity в БД → подтверждает пользователю с inline-кнопками.

**Ожидаемое поведение**
```
Пользователь: «Добавь сертификат на массаж, до 30 июня»
Агент: ✅ Сохранил.
       Сертификат на массаж — до 30 июня.
       Напомню за 14 дней и за 3 дня.
       [Добавить файл]  [Изменить срок]  [OK]
```
Время ответа: < 5 секунд.

**Технические детали**

`main.py`:
- FastAPI app с `lifespan` (запуск scheduler, регистрация Telegram webhook)
- `/webhook/telegram` POST endpoint
- `/webhook/email` POST endpoint-заглушка (501)
- `/health` GET endpoint

`bot/client.py`:
- Обёртка над Telegram Bot API (httpx async)
- `send_message(chat_id, text, reply_markup=None)`
- `set_webhook(url)`
- `answer_callback_query(callback_query_id)`

`bot/handlers.py`:
- `handle_update(update)` — роутинг по типу update
- `/start` — приветственное сообщение
- Текстовое сообщение → `ingestion.process_text(text, chat_id)`
- Callback query → `handle_callback(callback_data, chat_id)`
- Нет бизнес-логики — только роутинг

`modules/ingestion.py`:
- `process_text(text, chat_id, db)` — основной entry point
- Сохраняет `raw_record` в `event_log` (action=`raw_input`)
- Вызывает `parser.extract_entity(text)`
- Сохраняет entity + checklist_items + reminders в БД через Life Object Store
- Вызывает `notifications.send_confirmation(entity, chat_id)`

`modules/parser.py`:
- `extract_entity(raw_text) -> EntityData`
- Читает промпт из `prompts/entity_parser.txt`
- Вызывает OpenAI API (gpt-4o, structured output / JSON mode)
- Возвращает: type, name, start_date, end_date, notes, checklist_items[], reminder_rules[]
- При ошибке парсинга — fallback: сохранить как `logistics` с оригинальным текстом

`prompts/entity_parser.txt`:
- Системный промпт на русском
- Определяет тип сущности, ключевые поля, checklist по умолчанию, правила напоминаний
- JSON schema в промпте

`modules/notifications.py` (минимальная версия для этого среза):
- `send_confirmation(entity, chat_id)` — подтверждение после capture
- `send_message(chat_id, text, buttons=None)` — базовый отправщик

**Критерии приемки**
- [x] `/health` возвращает 200
- [x] `/webhook/telegram` принимает update и не падает
- [x] Текстовое сообщение создаёт entity в БД
- [x] Entity Parser корректно определяет 3+ категории (certificate, trip, document)
- [x] Checklist items создаются для поездок и документов
- [x] Reminders создаются по правилам из парсера
- [x] Пользователь получает подтверждение с inline-кнопками
- [x] Callback `ok` обрабатывается без ошибки
- [x] event_log содержит запись о raw_input
- [x] Тесты: `test_ingestion.py` — мок OpenAI, проверка создания entity в БД
- [x] Тесты: `test_parser.py` — парсинг разных типов, fallback на ошибку
- [x] Тесты: `test_handlers.py` — роутинг updates, /start, callback
- [x] Тесты: `test_notifications.py` — мок Telegram client, проверка payload
- [x] `make check` проходит

**Решение**
- `bot/client.py`: async Telegram API клиент через httpx (send_message, set_webhook, answer_callback_query, get_file, download_file, make_inline_keyboard)
- `bot/handlers.py`: роутинг update → /start, текст → ingestion, callback → ok/attach/edit
- `modules/parser.py`: OpenAI JSON mode + fallback на logistics при любой ошибке; промпт из `prompts/entity_parser.txt`
- `prompts/entity_parser.txt`: системный промпт на русском с правилами напоминаний и чеклистов по категориям
- `modules/ingestion.py`: process_text → EventLog raw_input → extract_entity → Entity + ChecklistItems + Reminders → commit → send_confirmation
- `modules/notifications.py`: send_confirmation с inline-кнопками ok/attach/edit; send_message
- `main.py`: FastAPI с lifespan (webhook registration в prod), /health, /webhook/telegram, /webhook/email (501)
- 38 тестов: config(4) + models(7) + handlers(5) + ingestion(9) + notifications(6) + parser(7)

---

### [FEATURE-002] Срез 2: Reminder Engine + Scheduler

**Статус:** ✅ Done
**Приоритет:** High
**Компонент:** Reminders / Scheduler

**Описание проблемы**
Напоминания созданы, но никто их не проверяет и не отправляет. Нужен Scheduler с джобами и Reminder Engine.

**Ожидаемое поведение**
Каждый день в 09:00 пользователь получает точечные пуши по срочным объектам.
Каждый день в 09:05 получает дайджест с остальным.

**Технические детали**

`modules/reminders.py`:
- `create_reminders_for_entity(entity, db)` — создаёт reminders по правилам из entity
- `get_due_reminders(db) -> list[Reminder]` — все pending с trigger_date <= today
- `get_digest_reminders(db) -> list[Reminder]` — все digest_only на эту неделю
- `snooze_reminder(reminder_id, days, db)` — откладывает, пишет в event_log
- `mark_reminder_sent(reminder_id, db)` — статус sent + event_log
- Правила: `before_N_days`, `on_date`, `recurring_weekly`, `digest_only`
- Срочные (< 48 ч) — point push. Остальные — digest.

`scheduler.py`:
- APScheduler `AsyncIOScheduler`
- Старт в `lifespan` FastAPI
- Джоб `check_reminders`: 09:00 МСК — point pushes для срочных
- Джоб `send_digest`: 09:05 МСК — дайджест
- Джоб `lifecycle_check`: понедельник 10:00 — архивирование истёкших

`modules/notifications.py` (расширение):
- `send_reminder(reminder, entity, chat_id)` — умное напоминание с кнопками
- `send_digest(items, chat_id)` — форматированный дайджест ≤ 7 строк
- Кнопки: `done / later_7d / later_3d / open / ignore`

**Критерии приемки**
- [x] `reminders.create_reminders_for_entity` создаёт правильные reminders по типу объекта
- [x] `check_reminders` джоб отправляет пуши для reminders с trigger_date = today
- [x] `send_digest` джоб формирует и отправляет дайджест
- [x] `lifecycle_check` джоб архивирует entity с истёкшей end_date + 7 дней
- [x] Callback `later_7d` откладывает reminder и пишет в event_log
- [x] Callback `done` закрывает reminder и entity (если все checklist закрыты)
- [x] Дважды отправить одно напоминание невозможно (event_log guard)
- [x] Scheduler стартует в lifespan без ошибок
- [x] Тесты: `test_reminders.py` — создание по правилам, snooze, guard от повторов
- [x] Тесты: `test_scheduler.py` — мок jobs, проверка что джобы зарегистрированы
- [x] `make check` проходит

**Решение**
- `modules/reminders.py`: `get_due_reminders`, `get_digest_reminders`, `snooze_reminder` (создаёт новый pending + пишет event_log), `mark_reminder_sent`, `mark_entity_done` (закрывает entity + отменяет pending reminders), `is_urgent` (≤ 2 дня → push, остальное → digest), `is_already_reminded_today` (event_log guard)
- `modules/notifications.py`: расширен — `send_reminder` с кнопками done/snooze/ignore, `send_digest` с группировкой по срочности (Срочно / На этой неделе), форматирование дат и эмодзи по категориям
- `modules/lifecycle.py`: `archive_expired_entities` (end_date + 7 дней → archived, отменяет reminders), `get_stale_entities`
- `scheduler.py`: APScheduler AsyncIOScheduler, 3 джоба: `check_reminders` (09:00), `send_digest` (09:05), `lifecycle_check` (пн 10:00); старт в lifespan FastAPI
- `bot/handlers.py`: расширен — `done_`, `later_7d_`, `later_3d_`, `ignore_` callbacks
- 19 новых тестов: reminders(15) + scheduler(4)

---

### [FEATURE-003] Срез 3: Suggestion Engine + полный Notification Layer

**Статус:** ✅ Done
**Приоритет:** High
**Компонент:** Suggestions / Notifications

**Описание проблемы**
Напоминания отправляются, но без контекста и next_action. Агент — будильник, а не помощник.

**Ожидаемое поведение**
```
Полис истекает через 12 дней.
Следующий шаг: запросить продление.
Последний полис прикреплён.
[Открыть полис]  [Сайт страховой]  [Отложить 7 дней]  [Готово]
```

**Технические детали**

`modules/suggestions.py`:
- `enrich_reminder(reminder, entity, db) -> EnrichedReminder`
- Добавляет: `next_action`, связанные resources (файл, ссылка), inline-кнопки
- Для подарков: вызывает OpenAI для shortlist идей (промпт из `prompts/suggestion.txt`)
- Для сертификатов: предлагает сценарий использования
- Для поездок: показывает что не хватает из checklist
- Проактивные подсказки: «3 сертификата, этот сгорает раньше»

`prompts/suggestion.txt`:
- Промпт для генерации next_action и shortlist
- Контекст: тип объекта, даты, ресурсы, история

`modules/notifications.py` (финальная версия):
- Все методы из среза 2 + форматирование с Suggestion данными
- Дайджест группирует по срочности: Срочно / На этой неделе / По категориям
- Не более 7 пунктов в дайджесте

**Критерии приемки**
- [x] Каждое напоминание содержит next_action
- [x] Напоминания по документам содержат прикреплённый файл/ссылку если есть
- [x] Напоминания по подаркам содержат shortlist (OpenAI)
- [x] Напоминания по поездкам показывают что отсутствует в checklist
- [x] Дайджест группирует по срочности и не превышает 7 пунктов
- [x] Проактивная подсказка «сертификат сгорает раньше других» работает
- [x] Тесты: `test_suggestions.py` — мок OpenAI, проверка enrich для каждого типа
- [x] `make check` проходит

**Решение**
- `modules/suggestions.py`: `enrich_reminder` — загружает ресурсы, открытый чеклист, вызывает OpenAI (только для gift/certificate или когда есть дата); `build_proactive_hints` — сканирует entities, формирует подсказки (сертификаты по близости к сгоранию, застывшие подписки); fallback на default next_action при ошибке OpenAI
- `prompts/suggestion.txt`: системный промпт с правилами для shortlist (gift/certificate) и next_action (по типам)
- `modules/notifications.py`: финальная версия — `send_enriched_reminder` (next_action + missing_checklist + note + shortlist), `send_proactive_hints`, дайджест ≤ 7 пунктов с группировкой
- `scheduler.py`: `check_reminders_job` теперь вызывает `enrich_reminder` → `send_enriched_reminder`
- 7 новых тестов: certificate shortlist, gift ideas, trip missing checklist, resources, urgent note, OpenAI fallback, document without date

---

## 🟡 Medium Priority

### [FEATURE-004] Срез 4: Файлы (Cloudflare R2)

**Статус:** ✅ Done
**Приоритет:** Medium
**Компонент:** Storage / Bot

**Описание проблемы**
Пользователь хочет прикрепить фото полиса или PDF билета — агент должен сохранить файл и привязать к entity.

**Ожидаемое поведение**
Пользователь пересылает фото/PDF → агент сохраняет в R2 → создаёт resource → парсит содержимое через OpenAI Vision → обновляет entity.

**Технические детали**

`modules/storage.py`:
- `upload_file(file_bytes, filename, entity_id) -> r2_key`
- `get_presigned_url(r2_key, expires=3600) -> str`
- boto3 с кастомным `endpoint_url` для Cloudflare R2

`bot/handlers.py` (расширение):
- Обработка `photo` и `document` в update
- Скачивает файл через Telegram API
- Передаёт в `ingestion.process_file(file_bytes, mime_type, chat_id)`

`modules/ingestion.py` (расширение):
- `process_file(file_bytes, mime_type, chat_id, db)`
- Загружает в R2, создаёт resource
- Вызывает parser с base64/текстом через OpenAI Vision

**Критерии приемки**
- [x] Фото из Telegram сохраняется в R2
- [x] PDF из Telegram сохраняется в R2
- [x] resource создаётся и привязывается к entity
- [x] OpenAI Vision извлекает поля из изображения документа
- [x] Presigned URL работает и доступен в notification
- [x] Тесты: `test_storage.py` — мок boto3, upload/get_url
- [x] Тесты: `test_ingestion.py` расширен — обработка файлов
- [x] `make check` проходит

**Решение**
- `modules/storage.py`: boto3 клиент с Cloudflare R2 endpoint, `upload_file` (UUID-ключ, content-type auto-detect), `get_presigned_url` (с configurable expires), `delete_file`
- `modules/ingestion.py`: `process_file` — upload → R2 → Resource; если без entity_id — извлекает текст (PDF через pdfplumber, фото через OpenAI Vision base64) → создаёт entity через process_text; `_extract_pdf_text`, `_extract_image_text`
- `bot/handlers.py`: `_handle_photo` (берёт largest photo, качает через Telegram API, передаёт в process_file), `_handle_document` (аналогично); caption с `#<id>` прикрепляет к существующей entity
- 6 тестов storage + 1 тест handler для фото

---

### [FEATURE-005] Срез 5: Lifecycle Management

**Статус:** ✅ Done
**Приоритет:** Medium
**Компонент:** Lifecycle

**Описание проблемы**
Объекты накапливаются. Нужен автоархив истёкших и monthly review для «подвисших» объектов.

**Ожидаемое поведение**
Раз в неделю: агент архивирует истёкшие объекты (end_date + 7 дней).
Раз в месяц: присылает список объектов, не обновлявшихся > 30 дней с кнопками «оставить / архивировать».

**Технические детали**

`modules/lifecycle.py`:
- `archive_expired_entities(db)` — entity с end_date + 7 дней → archived
- `get_stale_entities(db, days=30) -> list[Entity]` — не обновлялись > N дней
- `send_monthly_review(chat_id, db)` — список stale с кнопками
- Callback `archive_entity_{id}` → архивирует один объект
- Callback `keep_entity_{id}` → обновляет updated_at, убирает из stale

`scheduler.py` (расширение):
- `lifecycle_check` уже зарегистрирован — дополнить вызовом lifecycle.archive_expired_entities
- Новый джоб `monthly_review`: 1-е число каждого месяца, 10:00

**Критерии приемки**
- [x] entity с истёкшей end_date + 7 дней автоматически архивируется
- [x] Напоминания для archived entity прекращаются
- [x] Monthly review присылается раз в месяц
- [x] Кнопка «Архивировать» архивирует entity
- [x] Кнопка «Оставить» обновляет updated_at
- [x] Все операции фиксируются в event_log
- [x] Тесты: `test_lifecycle.py` — archive, stale detection, callback handling
- [x] `make check` проходит

**Решение**
- `modules/lifecycle.py`: `archive_expired_entities` (end_date + 7д, отменяет pending reminders, пишет event_log), `get_stale_entities` (active/paused, не обновлялись >30д), `send_monthly_review` (текстовый отчёт в Telegram)
- `scheduler.py`: добавлен `monthly_review_job` (1-е число, 10:00) — 4-й джоб
- 11 тестов: archive grace period, без end_date, cancel reminders, event_log, stale detection, scheduler jobs

---

### [FEATURE-006] Срез 6: Google Calendar (перенос из calendar_bot)

**Статус:** ✅ Done
**Приоритет:** Medium
**Компонент:** Integrations

**Описание проблемы**
Google Calendar уже реализован в calendar_bot. Нужно перенести и адаптировать.

**Ожидаемое поведение**
- При capture поездки или важной даты — автоматически создаётся событие в Google Calendar
- При создании reminder — проверяется конфликт с существующими событиями
- Команда «добавь в календарь» → `create_event` напрямую

**Технические детали**

Перенести из `/Users/vdobrynin/Documents/calendar_bot/bot/`:
- `google_auth.py` → `modules/google_auth.py`
  - Единственная правка: `from bot.config import config` → `from config import settings`
  - Убрать scope Tasks, оставить только Calendar
- `google_calendar.py` → `modules/google_calendar.py`
  - Правка импорта аналогично

Не переносить: `google_tasks.py`, `handlers.py`, `openai_client.py`, `voice.py`

Интеграция:
- `modules/suggestions.py` вызывает `google_calendar.create_event` для поездок
- `modules/reminders.py` вызывает `google_calendar.list_events` для проверки конфликтов
- `bot/handlers.py`: команда «добавь в календарь» → прямой вызов

`modules/google_auth.py` — scope только Calendar (убрать Tasks).

**Критерии приемки**
- [x] `google_auth.py` перенесён и адаптирован (import + scope)
- [x] `google_calendar.py` перенесён и адаптирован
- [x] При создании trip entity → событие создаётся в Google Calendar
- [x] Команда «добавь в календарь [текст]» работает
- [x] Конфликты проверяются при создании reminder
- [x] Тесты: `test_google_calendar.py` — по образцу calendar_bot (mock build + credentials)
- [x] `make check` проходит

**Решение**
- `modules/google_auth.py`: `from bot.config import config` → `from config import settings`; убран scope Tasks
- `modules/google_calendar.py`: полный перенос, адаптированы импорты
- `exceptions.py`: `GoogleAPIError` в корне проекта
- 11 тестов по паттерну calendar_bot (mock build + get_credentials)

---

### [UX-001] State machine для прикрепления файлов

**Статус:** 🆕 To Do
**Приоритет:** Medium
**Компонент:** Bot / Storage

**Описание проблемы**
Кнопка 📎 отвечает "Отправь файл с caption #ID". Пользователь не знает ID и не должен его знать. Если файл отправлен без caption — создаётся новый объект вместо прикрепления к существующему.

**Ожидаемое поведение**
После нажатия 📎 бот входит в режим ожидания файла и сам хранит `entity_id`. Следующий входящий файл автоматически прикрепляется к нужному объекту без caption.

**Технические детали**
- Новая таблица `bot_state` (или запись в `event_log`) — хранит `{chat_id, state, entity_id, expires_at}`
- В `bot/handlers.py` перед обработкой файла проверять активное состояние
- Состояние сбрасывается после прикрепления файла или через 10 минут

**Критерии приемки**
- [ ] После нажатия 📎 бот запоминает `entity_id` в состоянии
- [ ] Следующий файл от пользователя прикрепляется к нужному объекту без caption
- [ ] Состояние сбрасывается через 10 минут
- [ ] Состояние сбрасывается сразу после успешного прикрепления файла
- [ ] Caption `#ID` по-прежнему работает как fallback
- [ ] Тесты: проверка state transition, timeout, прикрепление
- [ ] `make check` проходит

---

## 🟢 Low Priority

### [FEATURE-007] Срез 7: API-заглушки для Web UI

**Статус:** ✅ Done
**Приоритет:** Low
**Компонент:** API

**Описание проблемы**
Web UI не в MVP, но архитектурные заглушки должны быть с первого дня чтобы потом только дописать логику.

**Ожидаемое поведение**
Все `/api/*` роуты возвращают `501 Not Implemented` с JSON `{"detail": "Not implemented"}`.
Роуты защищены API_KEY через `verify_token` dependency.

**Технические детали**

`api/auth.py`:
- `verify_token(x_api_key: str = Header(...))` dependency
- Сравнивает с `settings.api_key`
- Raises 401 если не совпадает

`api/routes.py`:
- `GET /api/entities` → 501
- `GET /api/entities/{id}` → 501
- `POST /api/entities` → 501
- `PATCH /api/entities/{id}` → 501
- `POST /api/entities/{id}/archive` → 501
- `GET /api/reminders` → 501
- `GET /api/contacts` → 501
- `GET /api/digest/preview` → 501

**Критерии приемки**
- [x] Все роуты возвращают 501 с правильным JSON
- [x] Без API_KEY заголовка возвращается 401
- [x] С неверным API_KEY возвращается 401
- [x] Тесты: `test_api.py` — 501 для всех роутов, 401 без токена
- [x] `make check` проходит

**Решение**
- `api/auth.py`: `verify_token` dependency, проверяет `x-api-key` header
- `api/routes.py`: 8 роутов (entities CRUD, reminders, contacts, digest/preview), все возвращают 501
- `main.py`: `app.include_router(api_router)`
- 25 тестов через httpx ASGITransport: 8×501, 8×422 (missing header), 8×401 (wrong key), /health

---

## 🧊 Icebox

### [FEATURE-008] Email inbound через Mailgun

**Статус:** 🆕 To Do
**Приоритет:** Low
**Компонент:** Bot / Ingestion

**Описание проблемы**
Пользователь пересылает письмо на Mailgun-адрес → агент парсит содержимое и создаёт entity.

**Ожидаемое поведение**
Пересланное письмо (бронь, билет, счёт) → entity в БД → подтверждение в Telegram.

**Технические детали**
- `bot/email_handler.py`: принимает Mailgun inbound POST
- Извлекает from, subject, body (text + html)
- Передаёт в `ingestion.process_text` (тот же поток что и Telegram)
- Настройка Mailgun: входящие письма → POST на `/webhook/email`

**Почему в Icebox**
Требует настройки домена и Mailgun account. Не блокирует MVP-ценность.

**Критерии приемки**
- [ ] `/webhook/email` принимает Mailgun payload без 500
- [ ] Письмо создаёт entity в БД
- [ ] Подтверждение отправляется в Telegram
- [ ] Тесты: `test_email_handler.py`
- [ ] `make check` проходит

---

### [FEATURE-009] Web UI (v2)

**Статус:** 🆕 To Do
**Приоритет:** Low
**Компонент:** Frontend

**Описание проблемы**
Визуальный кабинет для просмотра и управления объектами. Не в MVP.

**Технические детали**
- Реализация /api/* роутов (убрать 501)
- Frontend (React или Next.js — решить при старте)
- Auth через API_KEY или OAuth

**Критерии приемки**
- [ ] /api/* роуты возвращают реальные данные
- [ ] UI показывает список entities с фильтрами
- [ ] UI позволяет создавать и редактировать entities

---

### [UX-002] Страница объекта с прикреплёнными файлами (Web UI)

**Статус:** 🆕 To Do
**Приоритет:** Low
**Компонент:** API / Frontend

**Описание проблемы**
В веб-кабинете нет возможности посмотреть файлы, прикреплённые к объекту. Особенно критично для составных объектов (поездка: билет + бронь + страховка).

**Ожидаемое поведение**
На странице карточки объекта — список прикреплённых файлов из таблицы `resources`:
- имя файла, тип (`file` / `link`), дата прикрепления
- кнопка скачать / открыть (signed URL из Cloudflare R2)

**Технические детали**
- Реализовать эндпоинт `GET /api/entities/{id}/resources` (сейчас заглушка 501)
- Signed URL генерировать через `modules/storage.get_presigned_url`
- Реализовывать в рамках FEATURE-009 (Web UI), одним из первых эндпоинтов

**Критерии приемки**
- [ ] `GET /api/entities/{id}/resources` возвращает список ресурсов с полями: `filename`, `type`, `url`, `created_at`
- [ ] Для `r2_key` — URL генерируется как presigned (1 час)
- [ ] Для `url` — возвращается напрямую
- [ ] UI показывает список файлов на карточке объекта
- [ ] `make check` проходит

---

### [FEATURE-010] Мультипользовательность (v2)

**Статус:** 🆕 To Do
**Приоритет:** Low
**Компонент:** Auth / Data

**Описание проблемы**
owner_id в entities заложен, но логика одного пользователя. В v2 — семейный граф.

**Технические детали**
- Таблица users
- owner_id → реальный FK на users.id
- Семейные объекты: shared_with[]

---

## Прогресс по срезам

| Срез | Задача | Статус |
|------|--------|--------|
| 0 | TECH-001: Инициализация проекта | ✅ Done |
| 0 | TECH-002: Config + DB + Models | ✅ Done |
| 1 | FEATURE-001: Telegram Capture → БД | ✅ Done |
| 2 | FEATURE-002: Reminder Engine + Scheduler | ✅ Done |
| 3 | FEATURE-003: Suggestion Engine + Notifications | ✅ Done |
| 4 | FEATURE-004: Cloudflare R2 Storage | ✅ Done |
| 5 | FEATURE-005: Lifecycle Management | ✅ Done |
| 6 | FEATURE-006: Google Calendar | ✅ Done |
| 7 | FEATURE-007: API Stubs | ✅ Done |
| — | UX-001: State machine для файлов | 🆕 To Do |
| — | FEATURE-008: Email inbound (Mailgun) | 🧊 Icebox |
| — | FEATURE-009: Web UI | 🧊 Icebox |
| — | UX-002: Файлы на карточке объекта (Web UI) | 🧊 Icebox |
| — | FEATURE-010: Multi-user | 🧊 Icebox |
