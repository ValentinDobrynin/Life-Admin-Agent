# Life Admin Agent — Backlog

> Единственный канонический список задач проекта.
> После полной перестройки (apr 2026) старые задачи удалены вместе с подсистемами.

---

## Архитектурные решения (зафиксировано)

| Вопрос | Решение |
|--------|---------|
| Назначение сервиса | Хранилище личных данных и документов с естественно-языковым интерфейсом |
| Telegram | httpx + Bot API, webhook |
| Scheduler | APScheduler внутри FastAPI, 1 job (expiry_check) |
| Render tier | Starter |
| База | PostgreSQL (prod), aiosqlite (tests) |
| Модель данных | typed: Person, Document, Vehicle, Address, Note + BotState |
| Поиск | LLM-only over compact index, без embeddings |
| Файлы | Cloudflare R2 |
| OCR | OpenAI Vision (gpt-4o) |
| PDF | pypdf + pdfplumber fallback |
| Промпты | Только в `prompts/*.txt` |
| Удаление | Через `/delete <id>` (hard) или авто `status='replaced'` для document |

---

## 🟢 Backlog

### [FEATURE-201] Bulk-export данных в zip-архив

**Status:** 🆕 To Do
**Priority:** Low
**Component:** `bot/handlers.py`, `modules/storage.py`

**Problem Description**
Сейчас данные доступны только через выдачу по запросу. Для бэкапа нужен механизм скачать всё одним архивом.

**Expected Behavior**
Команда `/export` → бот собирает все записи + файлы из R2 в zip → отдаёт ссылкой (presigned URL).

**Technical Details**
- Новый модуль `modules/export.py`
- zip содержит `data.json` (все записи в JSON) и `files/` (файлы, организованные по `type/id/`)

**Acceptance Criteria**
- [ ] `/export` создаёт zip и отдаёт URL
- [ ] Архив содержит все записи и все файлы
- [ ] `make check` проходит

---

### [FEATURE-202] Bulk-import из zip-архива

**Status:** 🆕 To Do
**Priority:** Low
**Component:** `bot/handlers.py`

**Problem Description**
Парный к FEATURE-201 — позволяет восстановить состояние из zip.

**Acceptance Criteria**
- [ ] Команда `/import` принимает zip-файл
- [ ] Записи и файлы восстанавливаются
- [ ] `make check` проходит

---

### [UX-101] Поиск по тегам через `/tag <tag>`

**Status:** 🆕 To Do
**Priority:** Low
**Component:** `bot/handlers.py`, `modules/search.py`

**Problem Description**
Иногда удобно посмотреть всё с тегом «семья» или «FR-виза-2025» без LLM.

**Acceptance Criteria**
- [ ] Команда `/tag X` возвращает список записей с tag=X
- [ ] `make check` проходит

---

### [UX-102] Векторный поиск (embeddings + pgvector)

**Status:** 🆕 To Do
**Priority:** Low
**Component:** `modules/search.py`, `models.py`

**Problem Description**
Когда количество записей вырастет до сотен и тысяч, compact-index уже не влезет в контекст LLM.

**Acceptance Criteria**
- [ ] Включается, если `len(records) > N` (порог в config)
- [ ] embeddings строятся при ingest
- [ ] `make check` проходит

---

## ✅ Done

### [ARCH-008] Зачистка: удалить устаревшие подсистемы

**Status:** ✅ Done
**Priority:** High
**Component:** root, `api/`, `bot/`, `modules/`, `prompts/`, `migrations/`

**Resolution**
Удалены: `api/` (заглушки + API_KEY), `bot/email_handler.py`, `modules/google_auth.py`, `modules/google_calendar.py`, `modules/lifecycle.py`, `modules/suggestions.py`, `modules/reminders.py`, `modules/reference.py`, `modules/entity_view.py`, `modules/ingestion.py`, `modules/parser.py`, все старые `prompts/*.txt` кроме `soul.txt` (он будет переписан позже), `OPENCLAW_INTEGRATION.md`, `field_tests_full.md`, `test.db`, все старые миграции, все старые тесты. ENV `MAILGUN_*`, `GOOGLE_*`, `API_KEY` убраны из `config.py`, `.env.example`, `render.yaml`. Обновлены `requirements.txt`, `Makefile`, `project_description.md` (полная переписка), backlog (этот файл).

---

### [TECH-001] Новые модели + initial migration

**Status:** ✅ Done
**Priority:** High
**Component:** `models.py`, `migrations/versions/0001_initial.py`

**Resolution**
Введены 6 таблиц: `person`, `document`, `vehicle`, `address`, `note`, `bot_state`. У `document` поле `status: active | replaced`. У всех типизированных записей: `tags JSON`, `files JSON`, `created_at`, `updated_at`. Initial migration `0001_initial.py` создаёт схему с нуля.

---

### [FEATURE-101] Storage / Vision / PDF

**Status:** ✅ Done
**Priority:** High
**Component:** `modules/storage.py`, `modules/vision.py`, `modules/pdf.py`

**Resolution**
- `storage.py`: upload_file, download_file, get_presigned_url, delete_file. Прозрачный `r2_key` формата `{type}/{uuid}.ext`.
- `vision.py`: `ocr_image(image_bytes, mime) -> str` через OpenAI Vision (gpt-4o).
- `pdf.py`: `extract_text(pdf_bytes) -> str` через pypdf, fallback на vision.py если текст пустой (скан).

---

### [FEATURE-102] Ingest pipeline

**Status:** ✅ Done
**Priority:** High
**Component:** `modules/ingest.py`, `modules/cards.py`, `modules/state.py`, `prompts/classify.txt`, `prompts/patch.txt`

**Resolution**
- `ingest.py`: `ingest_text`, `ingest_files`, `confirm_draft`, `edit_draft`, `resolve_duplicate`. Определение `kind=note` → save сразу (skip verification).
- `cards.py`: `render_verification_card`, `render_record_card` (HTML для TG).
- `state.py`: CRUD bot_state с TTL 10 минут.
- `classify.txt`: возвращает `{type, kind?, owner_relation?, fields, tags, suggested_title}`.
- `patch.txt`: применяет user phrase к draft JSON, возвращает обновлённый JSON.
- Album buffering реализован через `media_group_id` + asyncio task с задержкой 1.5с.
- State machine: `awaiting_more_photos`, `awaiting_ocr_verification`, `awaiting_ocr_edit`, `awaiting_dup_resolution`.
- Duplicate detection: SQL по (kind, owner_person_id, status='active'). Кнопки `🆕 Новый` / `📎 Дополнить` / `♻️ Заменить`.

---

### [FEATURE-103] Search / retrieve

**Status:** ✅ Done
**Priority:** High
**Component:** `modules/search.py`, `prompts/retrieve.txt`

**Resolution**
- `search.py`: `build_index()` — компактный JSON всех `active` записей; `resolve_query(text)` → ids + action; `get_record(id)` — карточка + файлы.
- `retrieve.txt`: возвращает `{ids: [...], action: send_files | send_text | both | clarify, clarify_question?: str}`.
- При >1 → state `awaiting_retrieve_choice` + кнопки с `title` записей.
- Команда `/get <id>` — прямая выдача без LLM.

---

### [FEATURE-104] Bot handlers (router only)

**Status:** ✅ Done
**Priority:** High
**Component:** `bot/handlers.py`, `bot/client.py`

**Resolution**
- `bot/client.py` расширен: `send_photo`, `send_media_group`, `edit_message_text`.
- `bot/handlers.py` — три точки входа: `ingest_path` (текст/файлы), `query_path` (текст без активного state), `callback_router` (все inline-кнопки). Команды `/get <id>`, `/delete <id>`, `/list`, `/help`. Никакой бизнес-логики — только маршрутизация.

---

### [FEATURE-105] Expiry check job

**Status:** ✅ Done
**Priority:** High
**Component:** `scheduler.py`, `modules/notifications.py`

**Resolution**
- `scheduler.py` упрощён до 1 job — `expiry_check_job`, cron 09:00 в `settings.timezone`.
- `notifications.py`: `send_text`, `send_files`, `send_expiry_digest`. Дайджест истекающих (≤ `EXPIRY_WINDOW_DAYS`) с кнопками `📨 Прислать` для каждого документа.
- Только `document.status='active'` попадает в выборку.

---

### [ARCH-009] Новый prompts/soul.txt

**Status:** ✅ Done
**Priority:** Medium
**Component:** `prompts/soul.txt`

**Resolution**
`soul.txt` переписан под storage-режим: роль («хранилище личных данных»), тон (без реверансов, по делу), формат (ДД.ММ.ГГГГ, HTML-разметка), автономность (что выполняем молча vs спрашиваем), поведение при неоднозначности (best-guess + сообщить).
