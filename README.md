# Life Admin Agent

Персональное хранилище личных данных и документов с естественно-языковым интерфейсом через Telegram.

## Что делает

- Принимает текст, фото, PDF и альбомы фотографий через Telegram.
- Распознаёт содержимое (Vision OCR + PDF text extraction) и классифицирует в Person / Document / Vehicle / Address / Note.
- Перед сохранением показывает карточку с распознанными полями: подтвердить или править свободной фразой.
- По запросу («пришли паспорт жены», «где живёт Саша») отдаёт нужный файл и текстовую выжимку.
- Утренний пуш если что-то истекает в ближайшие 30 дней.

Подробное описание — в [project_description.md](./project_description.md).
Список задач — в [backlog.md](./backlog.md).

## Стек

Python 3.11 / FastAPI / SQLAlchemy 2 / Alembic / APScheduler / OpenAI gpt-4o (text + Vision) / Cloudflare R2 / Render Web Service.

## Локальный запуск

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env  # заполнить значения
make migrate
make run
```

## Команды make

- `make check` — `lint` + `types` + `test` (canonical, всегда зелёный)
- `make format` — ruff format + ruff check --fix
- `make lint` — ruff format --check + ruff check
- `make types` — mypy
- `make test` — pytest
- `make migrate` — alembic upgrade head
- `make run` — uvicorn с reload
