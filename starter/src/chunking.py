"""Deterministic fixed-window and paragraph-aware chunking."""
import hashlib
import re

from .models import Chunk, DocumentPage


def _make_chunk(
    page: DocumentPage, text: str, start: int, end: int,
    method: str, index: int,
) -> Chunk:
    """Create a traceable chunk; offsets refer to characters in page.text."""
    identity = f"{page.document_id}:{page.page}:{method}:{start}:{end}"
    chunk_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    metadata = {
        **page.metadata,
        "chunking_method": method,
        "chunk_index": index,
        "char_start": start,
        "char_end": end,
    }
    return Chunk(
        chunk_id=chunk_id,
        document_id=page.document_id,
        source=page.source,
        page=page.page,
        text=text,
        metadata=metadata,
    )


def _word_boundary(text: str, start: int, limit: int) -> int:
    """Prefer a whitespace boundary in the latter half of a window."""
    if limit >= len(text):
        return len(text)
    boundary = text.rfind(" ", start + max(1, (limit - start) // 2), limit)
    if boundary > start:
        return boundary
    # A long token/code string can have no whitespace; keep the window bounded.
    return limit


def fixed_size(page: DocumentPage, size: int = 500, overlap: int = 50) -> list[Chunk]:
    """Split a page into overlapping character windows (not token counts).

    Offsets are relative to the original page text. Window boundaries prefer
    whitespace, and overlap is measured from the final chosen boundary.
    """
    if size < 1 or overlap < 0 or overlap >= size:
        raise ValueError("size must be positive and overlap must be in [0, size)")

    text = page.text
    if not text.strip():
        return []

    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        limit = min(len(text), start + size)
        end = _word_boundary(text, start, limit)
        piece = text[start:end].strip()
        if piece:
            chunks.append(_make_chunk(
                page, piece, start, end, "fixed", len(chunks)
            ))
        if end >= len(text):
            break
        next_start = end - overlap
        # Guarantee progress even for unusual whitespace or overlap settings.
        start = max(start + 1, next_start)

    return chunks


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    """Return trimmed paragraph spans while retaining original offsets."""
    spans: list[tuple[int, int]] = []
    cursor = 0

    for separator in re.finditer(r"\n[ \t]*\n+", text):
        raw_start, raw_end = cursor, separator.start()
        while raw_start < raw_end and text[raw_start].isspace():
            raw_start += 1
        while raw_end > raw_start and text[raw_end - 1].isspace():
            raw_end -= 1
        if raw_start < raw_end:
            spans.append((raw_start, raw_end))
        cursor = separator.end()

    raw_start, raw_end = cursor, len(text)
    while raw_start < raw_end and text[raw_start].isspace():
        raw_start += 1
    while raw_end > raw_start and text[raw_end - 1].isspace():
        raw_end -= 1
    if raw_start < raw_end:
        spans.append((raw_start, raw_end))

    return spans


def _split_long_span(text: str, start: int, end: int, max_size: int) -> list[tuple[int, int]]:
    """Split an oversized paragraph at whitespace, keeping source offsets."""
    spans: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        limit = min(end, cursor + max_size)
        boundary = _word_boundary(text, cursor, limit)
        if boundary <= cursor:
            boundary = limit
        spans.append((cursor, boundary))
        cursor = boundary
        while cursor < end and text[cursor].isspace():
            cursor += 1
    return spans


def by_paragraph(page: DocumentPage, max_size: int = 1200) -> list[Chunk]:
    """Pack whole paragraphs up to a character limit; split oversized ones.

    Paragraphs are separated by blank lines. Chunks never cross page boundaries,
    so every result retains the page and chapter metadata from its source page.
    """
    if max_size < 1:
        raise ValueError("max_size must be positive")

    text = page.text
    if not text.strip():
        return []

    paragraphs = _paragraph_spans(text)
    grouped: list[tuple[int, int]] = []
    group_start: int | None = None
    group_end: int | None = None

    for para_start, para_end in paragraphs:
        para_length = para_end - para_start
        if para_length > max_size:
            if group_start is not None and group_end is not None:
                grouped.append((group_start, group_end))
                group_start = group_end = None
            grouped.extend(_split_long_span(text, para_start, para_end, max_size))
            continue

        proposed_length = (
            para_end - group_start if group_start is not None else para_length
        )
        if group_start is not None and proposed_length > max_size:
            grouped.append((group_start, group_end))
            group_start = para_start
        elif group_start is None:
            group_start = para_start
        group_end = para_end

    if group_start is not None and group_end is not None:
        grouped.append((group_start, group_end))

    return [
        _make_chunk(
            page, text[start:end].strip(), start, end, "paragraph", index
        )
        for index, (start, end) in enumerate(grouped)
        if text[start:end].strip()
    ]
