# OpenClaw Integration Plan

Этот документ фиксирует, какие элементы Life Admin Agent мы переносим в текущий OpenClaw-стек и как будем их разворачивать.

## 1. Что забираем и как используем

| Блок | Что берём | Как используем в OpenClaw |
| ---- | --------- | ------------------------- |
| **Модель данных** | `models.py` (entities, reminders, checklist_items, contacts, resources, event_log) + миграции Alembic | Разворачиваем ту же схему в Render Postgres, чтобы единообразно хранить документы/поездки/сертификаты. Event log остаётся неизменным → пригодится для аудита и анти-спама напоминаний. |
| **Scheduler** | `scheduler.py` (APScheduler + задачи check_reminders, send_digest, lifecycle_check) | Переносим внутрь единого FastAPI сервиса. Сами задачи синхронизируем с OpenClaw heartbeats: heartbeat → «живой» мониторинг, APScheduler → именно отложенные действия. |
| **Pipeline ingest → parser → reminders** | `modules/ingestion.py`, `modules/parser.py`, `prompts/entity_parser.txt`, `modules/reminders.py`, `modules/suggestions.py` | Подключаем к источникам (Telegram, email, фото). Все входящие сообщения гоняем через ingestion → parser, получаем Entity → создаём reminders/checklists. suggestions выдаёт inline-кнопки и next_actions. |
| **Уведомления** | `modules/notifications.py` + `bot/handlers.py` | Notifications становится единственной точкой отправки телеграм-сообщений: ежедневные дайджесты, срочные пуши, inline-действия. В хэндлерах остаётся только маршрутизация и валидация сигнатур вебхука. |
| **Google Calendar** | `modules/google_auth.py`, `modules/google_calendar.py` | Меняем текущие calendar-заглушки на этот модуль: синхронно создаём события, привязываем напоминания и читаем конфликты. Используем те же ENV (`GOOGLE_CLIENT_ID/SECRET/REFRESH_TOKEN`). |
| **Хранилище файлов** | `modules/storage.py` (Cloudflare R2) | Все документы (полисы, сертификаты, чеки) выгружаем в R2 через этот модуль; в БД храним `resources.r2_key`. |
| **Lifecycle & Goal Monitor** | `modules/lifecycle.py`, `modules/goal_monitor.py` (часть scheduler) | Службы автоархива, ежемесячных обзоров и периодического мониторинга целей подключаем к APScheduler. |
| **Mailgun inbound** | `bot/email_handler.py` | Подключаем Mailgun webhook → ingestion, чтобы можно было перекидывать письма. |
| **API заглушки** | `api/routes.py`, `api/auth.py` | Пока Web UI нет — оставляем 501-заглушки, но готовим авторизацию по токену (`API_KEY`) для будущих панелей. |

Дополнительно переносим подходы:
- «Никакой бизнес-логики в роутерах» — в OpenClaw-стеке также держим логику только в `modules/*`.
- «Не удалять, а архивировать» — все soft-delete флаги сохраняем.
- Дайджест как первичный канал, срочные пуши — только при SLA < 48h.

## 2. План развёртывания в OpenClaw

### 2.1 Сервисы
- **Web Service на Render**: один FastAPI (`main.py`) с webhook’ами Telegram/Mailgun и APScheduler внутри. План: `starter` билд, `pip install -r requirements.txt`, `bash start.sh` (совместимо с имеющимися сервисами).
- **Postgres (Render managed)**: можно reuse текущий workspace DB или поднять отдельную. Требуются расширения timezone/uuid (SQLAlchemy уже настроен).
- **Cloudflare R2**: используем существующий аккаунт, в ENV передаём `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`.

### 2.2 Интеграции
- **Telegram**: webhook (aiogram) уже в проекте. В OpenClaw конфиге прописываем бота + Webhook URL `https://<render-app>/telegram/webhook`.
- **Mailgun**: inbound route → POST `/webhook/email`.
- **Google Calendar**: хранить учётки в `.env.google` (уже заведено в workspace). Модуль `google_calendar` используется всеми напоминаниями.
- **OpenAI (gpt-4o)**: ключ в `.env`, используется parser/suggestions. Можно централизовать через OpenClaw secrets.

### 2.3 Cron/Scheduler/Heartbeat
- APScheduler остаётся внутри FastAPI (как сейчас). 
- OpenClaw heartbeats: добавляем проверку статуса scheduler’а и логику «если задача зависла → ping в heartbeat». 
- Для редких задач (weekly review) можем также оформить `openclaw cron` (вне сервиса) → REST hook `/tasks/trigger`.

### 2.4 Деплой пайплайн
1. Регистрируем репозиторий `life-admin-agent` в Render (Web Service, Python). 
2. ENV/секреты: `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `OPENAI_API_KEY`, `MAILGUN_*`, `R2_*`, `GOOGLE_*`, `API_KEY`.
3. Build Command: `pip install -r requirements.txt`. Start Command: `bash start.sh` (как в проекте).
4. Postgres миграции — запуск `alembic upgrade head` на деплое (start.sh уже делает `alembic upgrade head`).
5. Ротация ключей/секретов делаем через OpenClaw secrets storage, чтобы не дергать Render UI.

### 2.5 Мониторинг и алерты
- Встраиваем отправку статуса в OpenClaw heartbeat (например, запись «LifeAdmin OK / очередей N»).
- При ошибках scheduler’а или неудачных инсертах → уведомление в Telegram через notifications.
- Логи Render читаем тем же MCP клиентом, что уже настроили для FoodBot.

### 2.6 Совместимость с текущими сервисами
- **FoodBot**: остаётся отдельным сервисом, но может отправлять structured events в ingestion (через REST или direct DB) → единый storage.
- **calendar_bot**: частично заменяется новым Google-модулем из Life Admin Agent, чтобы не держать дубли кода.
- **Notion/рабочая память**: можно зеркалить важные entities в Notion CLI, используя event_log как источник.

## 3. Следующие шаги
1. Настроить ENV/секреты в workspace (`.env.google`, `.env.render`, OpenClaw secrets).
2. Подготовить Render Postgres (схема уже в migrations).
3. Задеплоить сервис, проверить webhook’и Telegram/Mailgun.
4. Настроить heartbeat отчёты и MCP-логи для нового сервиса.
5. Постепенно мигрировать источники (Telegram, email, фото) на ingestion пайплайн; убедиться, что reminders/digest работают.
