"""Tag editing utilities for already-saved records.

Supports three syntaxes from a single user phrase:

* ``"новый, второй, заграна"`` — replace the whole list (comma-separated).
* ``"+загран +первый"`` — additive only.
* ``"-старый -ненужный"`` — remove only.
* ``"+загран -старый"`` — mixed add/remove.

The result is **deduplicated** while preserving order: existing tags first,
then any new ones from the phrase.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

logger = logging.getLogger(__name__)


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        s = str(v).strip()
        if not s:
            continue
        key = s.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _split_tokens(phrase: str) -> list[str]:
    """Split by commas / semicolons / newlines and trim each piece."""
    pieces = re.split(r"[,\n;]+", phrase)
    return [p.strip() for p in pieces if p.strip()]


def apply_tag_edit(current: list[str] | None, phrase: str) -> list[str]:
    """Apply a free-form tag edit *phrase* to *current* tag list.

    Returns the new tag list (always a fresh list).
    """
    base = list(current or [])
    raw = phrase.strip()
    if not raw:
        return _dedupe(base)

    # Detect explicit additive/subtractive markers — when the phrase contains
    # `+` or `-` token prefixes we treat it as additive/subtractive instead of
    # full replacement.
    has_marker = re.search(r"(?:^|\s)[+\-−—](?=\S)", raw) is not None

    if not has_marker:
        # Pure replacement: only split on commas/newlines/semicolons so values
        # with spaces (e.g. "Иван Иванов") survive intact.
        return _dedupe(_split_tokens(raw))

    # Marker mode: split first by commas/newlines, then by whitespace inside
    # each piece, so "+загран +первый -старый" becomes 3 tokens.
    tokens: list[str] = []
    for piece in _split_tokens(raw):
        tokens.extend(p for p in re.split(r"\s+", piece) if p)

    add: list[str] = []
    remove: set[str] = set()
    for tok in tokens:
        if tok.startswith("+"):
            v = tok[1:].strip()
            if v:
                add.append(v)
        elif tok.startswith(("-", "−", "—")):
            v = tok[1:].strip()
            if v:
                remove.add(v.casefold())
        else:
            add.append(tok)

    kept = [t for t in base if t.casefold() not in remove]
    return _dedupe(kept + add)
