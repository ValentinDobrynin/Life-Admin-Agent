from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

SOUL_PATH = Path(__file__).parent.parent / "prompts" / "soul.txt"


def test_soul_file_exists_and_nonempty() -> None:
    assert SOUL_PATH.exists(), "prompts/soul.txt must exist"
    content = SOUL_PATH.read_text(encoding="utf-8")
    assert len(content.strip()) > 100, "soul.txt should be non-trivially long"


def test_soul_contains_required_sections() -> None:
    content = SOUL_PATH.read_text(encoding="utf-8")
    for section in ("Роль", "Тон", "Автономность", "неоднозначности"):
        assert section in content, f"soul.txt missing section: {section}"


@patch("modules.parser._get_client")
async def test_extract_entity_includes_soul_in_system_prompt(
    mock_get_client: MagicMock,
) -> None:
    """soul.txt content is prepended to the system prompt in extract_entity."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = (
        '{"type": "logistics", "name": "Тест", "start_date": null, '
        '"end_date": null, "notes": null, "checklist_items": [], "reminder_rules": []}'
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
    mock_get_client.return_value = mock_client

    from modules.parser import extract_entity

    await extract_entity("купить молоко")

    call_kwargs = mock_client.chat.completions.create.call_args
    messages = call_kwargs.kwargs.get("messages") or call_kwargs.args[0] if call_kwargs.args else []
    if not messages:
        messages = call_kwargs[1].get("messages", [])

    system_content = next(m["content"] for m in messages if m["role"] == "system")
    soul_content = SOUL_PATH.read_text(encoding="utf-8")

    # At least one key phrase from soul.txt must appear in the system prompt
    assert "операционный диспетчер" in system_content
    # The entity parser instructions must also be present
    assert "JSON" in system_content
    # Soul should come before the parser prompt
    assert system_content.index("операционный диспетчер") < system_content.index("JSON")
    _ = soul_content  # used for context


@patch("modules.reference._get_client")
@patch("modules.reference.get_all_reference", new_callable=AsyncMock)
@patch("modules.reference.get_owned_items", new_callable=AsyncMock)
async def test_generate_text_includes_soul_in_system_prompt(
    mock_owned: AsyncMock,
    mock_all_ref: AsyncMock,
    mock_get_client: MagicMock,
) -> None:
    """soul.txt content is prepended to the system prompt in generate_text."""
    from unittest.mock import MagicMock as MM

    ref_item = MM()
    ref_item.id = 1
    ref_item.label = "Паспорт"
    ref_item.type = "document"
    ref_item.relation = None
    ref_item.data = {"series": "1234 567890"}
    mock_all_ref.return_value = [ref_item]
    mock_owned.return_value = []

    mock_response = MM()
    mock_response.choices = [MM()]
    mock_response.choices[0].message.content = "Прошу выдать пропуск."
    mock_client = MM()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
    mock_get_client.return_value = mock_client

    db = AsyncMock()

    from modules.reference import generate_text

    await generate_text("напиши заявление", db)

    call_kwargs = mock_client.chat.completions.create.call_args
    messages = call_kwargs.kwargs.get("messages") or []
    if not messages:
        messages = call_kwargs[1].get("messages", [])

    system_content = next(m["content"] for m in messages if m["role"] == "system")

    assert "операционный диспетчер" in system_content
    assert (
        "справочник" in system_content.lower()
        or "контекст" in system_content.lower()
        or "данные" in system_content.lower()
    )
    assert system_content.index("операционный диспетчер") < system_content.rindex("---")
