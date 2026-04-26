# Life Admin Agent — Project Description

## Что строим

Персональное хранилище личных данных и документов с естественно-языковым интерфейсом через Telegram.

Пользователь присылает боту:
- свободный текст («Саша Балахнин живёт на Тверской 5-12, телефон ...»)
- фотографии (паспорт, права, страховка, СТС — одну или несколько в альбоме)
- PDF (сканы документов)

Бот:
1. распознаёт содержимое (Vision OCR / PDF text extraction),
2. классифицирует в одну из пяти типизированных сущностей,
3. показывает результат на верификацию,
4. сохраняет в БД и Cloudflare R2.

По запросу («пришли паспорт жены», «где живёт Саша», «срок прав?») — выдаёт нужную запись (файл + текстовая выжимка).

Один утренний пуш — если что-то из документов истекает в ближайшие 30 дней.

Главная ценность — единое место для бытовых документов и сведений с быстрой выдачей в Telegram.

## Для кого

Один пользователь + семья. Один аккаунт, никакой мультиюзерности. Продукт для себя, не SaaS.

## Стек

| Слой | Технология |
|------|-----------|
| Backend | Python 3.11+, FastAPI |
| База данных | PostgreSQL (Render managed), aiosqlite для тестов |
| ORM / миграции | SQLAlchemy 2 + Alembic |
| Планировщик | APScheduler (внутри FastAPI lifespan) |
| Telegram | httpx + raw Bot API (webhook, не polling) |
| LLM | OpenAI API (gpt-4o) — classify, patch, retrieve |
| Vision OCR | OpenAI Vision (через тот же gpt-4o) |
| PDF | pypdf (текст), fallback на pdfplumber |
| Файлы | Cloudflare R2 (boto3 с кастомным endpoint_url) |
| Деплой | Render Web Service, plan: starter |

## Архитектурные принципы

**1. Никакой бизнес-логики в роутерах.**
`bot/handlers.py` и `main.py` только принимают запрос и делегируют. Вся логика в `modules/`.

**2. Все внешние вызовы через свои модули.**
- OpenAI → `modules/ingest.py`, `modules/search.py`
- OpenAI Vision → `modules/vision.py`
- Telegram → `bot/client.py`
- Cloudflare R2 → `modules/storage.py`
- PDF → `modules/pdf.py`

Прямых вызовов внешних API из handlers/scheduler — нет.

**3. Типизированные записи, не «универсальный bag».**
Пять таблиц: `person`, `document`, `vehicle`, `address`, `note`. Каждая со своим набором полей. JSON-поля `fields`/`tags` — для гибкости внутри типа.

**4. Никогда не удаляем — помечаем `replaced`.**
У `document` есть `status: active | replaced`. При замене старая запись остаётся в БД, не выдаётся при retrieve.

**5. Каждое сообщение пользователю — action-oriented.**
Любой ответ заканчивается inline-кнопками или явным следующим шагом.

**6. Поиск через LLM без векторных БД.**
В контекст модели грузим компактный JSON всех `active` записей (id, тип, title, key fields, tags, owner). Модель сама выбирает релевантные. Без embeddings, без pgvector. Достаточно при объёме ≤ ~500 записей.

**7. State-machine в БД, не in-memory.**
Таблица `bot_state` хранит контекст диалога. TTL 10 минут. Переживает рестарты Render.

**8. Модули изолированы.**
Порядок зависимостей: `models → modules → bot → scheduler`. Циклов нет.

## Структура проекта

```
life-admin-agent/
├── main.py                  FastAPI + lifespan (запуск scheduler)
├── config.py                Pydantic Settings
├── database.py              SQLAlchemy engine + get_db
├── models.py                Person, Document, Vehicle, Address, Note, BotState
├── scheduler.py             APScheduler: 1 job — expiry_check
├── bot/
│   ├── client.py            Telegram Bot API: send_message, send_document,
│   │                        send_photo, send_media_group, edit_message_text,
│   │                        answer_callback_query, get_file
│   └── handlers.py          webhook router: ingest_path / query_path / callback_router
├── modules/
│   ├── storage.py           R2: upload_file, download_file, get_presigned_url
│   ├── vision.py            OpenAI Vision OCR — text from image bytes
│   ├── pdf.py               pypdf + fallback на vision для сканов
│   ├── cards.py             render verification/retrieval cards (HTML for TG)
│   ├── ingest.py            classify + extract + verification + duplicate-resolution
│   ├── search.py            LLM-only retrieval over compact index
│   ├── state.py             CRUD bot_state с TTL
│   └── notifications.py     send_text, send_files (single или альбом),
│                            send_expiry_digest
├── prompts/
│   ├── soul.txt             конституция агента (тон, формат)
│   ├── classify.txt         classify + extract structured fields
│   ├── patch.txt            apply user phrase as patch to draft JSON
│   └── retrieve.txt         resolve query → list of matching record ids
├── tests/                   pytest + pytest-asyncio (sqlite+aiosqlite)
└── migrations/              Alembic
    └── versions/
        └── 0001_initial.py
```

## Модель данных

Все таблицы имеют общие поля: `id`, `tags JSON`, `files JSON` (массив `{r2_key, filename, content_type}`), `created_at`, `updated_at`.

| Таблица | Уникальные поля |
|---|---|
| `person` | full_name, birthday, relation (жена/сын/мама/я/...), notes, fields JSON |
| `document` | kind (passport/driver_license/insurance/visa/certificate/contract/other), title, owner_person_id → person, issued_at, expires_at, status (active/replaced), fields JSON |
| `vehicle` | make, model, plate, vin, owner_person_id → person, fields JSON |
| `address` | label, person_id → person (опц.), country, city, street, fields JSON |
| `note` | title, body, fields JSON |
| `bot_state` | chat_id (PK), state, context JSON, expires_at |

`bot_state.state` ∈
- `awaiting_more_photos` — после первого одиночного фото, ждём «📎 Ещё / ✅ Готово»
- `awaiting_ocr_verification` — показана карточка, ждём «✅ Всё верно / ✏️ Исправить»
- `awaiting_ocr_edit` — после ✏️, ждём текстовое сообщение с правкой
- `awaiting_dup_resolution` — найден дубликат, ждём «🆕 Новый / 📎 Дополнить / ♻️ Заменить»
- `awaiting_retrieve_choice` — найдено несколько кандидатов при retrieve, ждём выбора кнопкой

## Поток ingest

```
1. Telegram webhook → handlers.ingest_path
2. Файлы (если есть) → R2 → r2_keys
3. Текст из источников: caption, OCR (vision.py), PDF (pdf.py)
4. Album buffering: media_group_id агрегируется ~1с в один draft
5. Single photo без альбома: state=awaiting_more_photos, ждём ✅ Готово
6. classify.txt: LLM → {type, kind, owner_relation, fields, tags, suggested_title}
7. Если type=note → save сразу (skip verification)
8. Иначе → state=awaiting_ocr_verification, send_card(draft) с [✅] [✏️]
9. ✅ → detect_duplicate (kind + owner_person_id, status='active')
   - если найден → state=awaiting_dup_resolution, [🆕 Новый] [📎 Дополнить] [♻️ Заменить]
   - иначе → save, ответ «Сохранил {title} · /<id>»
10. ✏️ → state=awaiting_ocr_edit, ask «Что исправить?»
11. На текст в awaiting_ocr_edit → patch.txt → goto 8
12. Hard delete: команда `/delete <id>` — удаляет запись и файлы из R2
```

## Поток retrieve

```
1. Telegram webhook → handlers.query_path
2. modules/search.build_index() → JSON всех записей (status='active')
3. retrieve.txt: LLM → {ids: [...], action: send_files | send_text | both | clarify}
4. 0 → «Ничего не нашёл по запросу»
5. 1 → отдать файлы (или альбом) + текстовая карточка из fields
6. >1 → state=awaiting_retrieve_choice, кнопки с title записей
7. Команда `/get <id>` — прямая выдача без LLM
```

## Категории объектов и их kind

- `person.relation`: я, жена, муж, сын, дочь, мама, папа, друг, коллега, иное
- `document.kind`: passport, driver_license, insurance, visa, certificate, contract, snils, inn, medical, other
- `vehicle`: только make/model/plate/vin — без kind
- `address.label`: дом, дача, работа, родители, иное

## Источники данных (MVP)

- Ручной ввод текста через Telegram
- Одиночные фото через Telegram (с подтверждением «ещё страница?»)
- Альбомы фото через Telegram (media_group_id)
- PDF через Telegram

## Что явно НЕ делаем

- **Нет email-приёма** — Mailgun выкинут.
- **Нет Google Calendar** — выкинут.
- **Нет polling** — только webhook.
- **Нет отдельного worker** — APScheduler внутри Web Service.
- **Нет мультиюзерности** — один аккаунт.
- **Нет автоплатежей и автопродления документов** — только напоминания.
- **Нет браузерного агента**.
- **Нет Web UI и /api/*** — выкинуты.
- **Нет встроенных чек-листов и suggestions/next_action** — выкинуты.
- **Нет вектора и embeddings** — пока хватает LLM-over-index.
- **Нет лет архивации/lifecycle** — только status=replaced на document.

## Переменные окружения

```
DATABASE_URL=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=
RENDER_URL=
TIMEZONE=Europe/Moscow
EXPIRY_WINDOW_DAYS=30      # дефолт; экспортируется в pdt expiry_check_job
```

## Timezone

Europe/Moscow — дефолт. В БД храним UTC, конвертируем при отображении.

## Язык

Сообщения пользователю — русский.
Код, комментарии, имена переменных — английский.
Промпты для OpenAI — русский.
