"""Read selected PDF pages from a corpus manifest and normalize their text."""
from pathlib import Path
import re
import unicodedata

from pypdf import PdfReader

from .models import DocumentPage


def normalize_text(text: str, printed_page: int | None = None) -> str:
    """Normalize Unicode and whitespace while preserving paragraph breaks."""
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]

    # Match the manifest's normalization: remove only a final printed page label.
    if printed_page is not None and lines and lines[-1] == str(printed_page):
        lines.pop()

    normalized = "\n".join(lines)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def load_pdf(path: Path, manifest: dict) -> list[DocumentPage]:
    """Extract manifest-selected pages, preserving printed and physical locations.

    `DocumentPage.page` is the printed page used by the benchmark. The physical
    PDF page and other source metadata are retained in `DocumentPage.metadata`.
    Pages with no extractable text are collected and reported together so they
    cannot silently disappear from ingestion.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")

    try:
        page_rows = manifest["pages"]
        source_id = manifest["source_id"]
        source_path = manifest["file"]["source_path"]
    except (KeyError, TypeError) as exc:
        raise ValueError("Manifest must contain source_id, file.source_path, and pages") from exc

    if not isinstance(page_rows, list) or not page_rows:
        raise ValueError("Manifest pages must be a non-empty list")

    reader = PdfReader(str(path))
    declared_pdf_pages = manifest.get("file", {}).get("physical_pdf_page_count")
    if declared_pdf_pages is not None and declared_pdf_pages != len(reader.pages):
        raise ValueError(
            f"PDF page count mismatch: manifest says {declared_pdf_pages}, "
            f"PDF contains {len(reader.pages)}"
        )

    pages: list[DocumentPage] = []
    unreadable: list[str] = []

    for row in page_rows:
        if not row.get("counted_as_useful_content", True):
            continue

        try:
            physical_page = int(row["physical_pdf_page"])
            printed_page = int(row["printed_page"])
            row_source_id = row["source_id"]
            page_id = row["page_id"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid page entry in manifest: {row!r}") from exc

        if row_source_id != source_id:
            raise ValueError(
                f"Page {page_id} has source_id {row_source_id!r}; expected {source_id!r}"
            )
        if physical_page < 1 or physical_page > len(reader.pages):
            raise ValueError(
                f"Page {page_id} points to physical PDF page {physical_page}, "
                f"outside 1..{len(reader.pages)}"
            )

        extracted = reader.pages[physical_page - 1].extract_text() or ""
        text = normalize_text(extracted, printed_page)
        if not text:
            unreadable.append(
                f"{page_id} (printed {printed_page}, physical {physical_page})"
            )
            continue

        metadata = {
            "page_id": page_id,
            "printed_page": printed_page,
            "physical_pdf_page": physical_page,
            "chapter_number": int(row["chapter_number"]),
            "chapter_title": row["chapter_title"],
        }
        pages.append(DocumentPage(
            document_id=source_id,
            source=source_path,
            page=printed_page,
            text=text,
            metadata=metadata,
        ))

    if unreadable:
        details = "; ".join(unreadable)
        raise ValueError(
            "No extractable text on selected manifest pages: " + details
            + ". OCR or replace these pages before continuing."
        )
    if not pages:
        raise ValueError("No useful pages were loaded from the manifest")

    return pages
