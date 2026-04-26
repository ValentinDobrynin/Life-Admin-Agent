from __future__ import annotations

from modules.tag_edit import apply_tag_edit


def test_replace_when_no_markers() -> None:
    assert apply_tag_edit(["паспорт", "passport"], "загран, первый") == ["загран", "первый"]


def test_replace_dedupes_case_insensitive() -> None:
    assert apply_tag_edit(None, "Загран, загран, ПЕРВЫЙ") == ["Загран", "ПЕРВЫЙ"]


def test_add_only_with_plus() -> None:
    assert apply_tag_edit(["паспорт"], "+загран +первый") == [
        "паспорт",
        "загран",
        "первый",
    ]


def test_remove_only_with_minus() -> None:
    assert apply_tag_edit(["паспорт", "старый", "passport"], "-старый") == [
        "паспорт",
        "passport",
    ]


def test_mixed_add_and_remove() -> None:
    assert apply_tag_edit(["паспорт", "старый"], "+загран -старый") == [
        "паспорт",
        "загран",
    ]


def test_remove_is_case_insensitive() -> None:
    assert apply_tag_edit(["Старый"], "-старый") == []


def test_empty_phrase_just_dedupes() -> None:
    assert apply_tag_edit(["a", "A", "b"], "  ") == ["a", "b"]


def test_unicode_dash_treated_as_remove() -> None:
    assert apply_tag_edit(["x", "y"], "—y") == ["x"]


def test_bare_token_in_mixed_phrase_is_added() -> None:
    assert apply_tag_edit(["паспорт"], "+загран новый") == [
        "паспорт",
        "загран",
        "новый",
    ]
