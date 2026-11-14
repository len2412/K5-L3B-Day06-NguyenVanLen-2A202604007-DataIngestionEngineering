"""Run ingestion, A/B/C retrieval, metrics, and manual-review preparation."""
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .chunking import by_paragraph, fixed_size
from .embeddings import LocalBgeEmbedder
from .evaluation import evaluate_query, summarize
from .ingestion import load_pdf
from .store import PgVectorStore


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def _write_json(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_pdf_extraction(path: Path, pages) -> None:
    """Save every normalized manifest page and its trace metadata as readable text."""
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(f"Extracted pages: {len(pages)}\n")
        stream.write("Text is normalized; printed and physical PDF locations come from the manifest.\n")
        for page in pages:
            stream.write("\n" + "=" * 100 + "\n")
            stream.write(
                f"page_id={page.metadata.get('page_id', '')} | "
                f"document_id={page.document_id} | source={page.source}\n"
            )
            stream.write(
                f"printed_page={page.page} | "
                f"physical_pdf_page={page.metadata.get('physical_pdf_page', '')} | "
                f"chapter_number={page.metadata.get('chapter_number', '')} | "
                f"chapter_title={page.metadata.get('chapter_title', '')} | "
                f"characters={len(page.text)}\n"
            )
            stream.write("-" * 100 + "\n")
            stream.write(page.text)
            stream.write("\n")


def _write_chunking_result(path: Path, chunks_by_strategy: dict[str, list]) -> None:
    """Save complete chunk text and metadata for the distinct chunking outputs."""
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("Chunking results\n")
        stream.write("A = fixed 500-character windows with 50-character overlap.\n")
        stream.write("B = paragraph-aware chunks, maximum 1200 characters.\n")
        stream.write("C reuses B's exact chunks and vectors; it differs only by query-time metadata filtering.\n")
        stream.write("Character offsets are relative to the normalized source page text.\n")

        for strategy in ("A", "B"):
            chunks = chunks_by_strategy[strategy]
            stream.write("\n" + "#" * 100 + "\n")
            stream.write(f"STRATEGY {strategy}: {len(chunks)} chunks\n")
            stream.write("#" * 100 + "\n")
            for chunk in chunks:
                stream.write("\n" + "-" * 100 + "\n")
                stream.write(
                    f"chunk_id={chunk.chunk_id} | document_id={chunk.document_id} | "
                    f"page={chunk.page} | characters={len(chunk.text)}\n"
                )
                stream.write(
                    f"metadata={json.dumps(chunk.metadata, ensure_ascii=False, sort_keys=True)}\n"
                )
                stream.write("text:\n")
                stream.write(chunk.text)
                stream.write("\n")


def _validate_benchmark(benchmark: dict[str, Any], manifest: dict[str, Any], pages) -> None:
    questions = benchmark.get("questions")
    if not isinstance(questions, list) or len(questions) < 20:
        raise ValueError("Benchmark must contain at least 20 questions")
    if benchmark.get("source_id") != manifest.get("source_id"):
        raise ValueError("Benchmark source_id does not match the manifest")

    splits = {split: sum(q.get("split") == split for q in questions) for split in ("dev", "test")}
    if splits["dev"] < 15 or splits["test"] < 5:
        raise ValueError(f"Need at least 15 dev and 5 test questions; found {splits}")
    filtered = [q for q in questions if q.get("filter")]
    if len(filtered) < 3 or not any(q.get("split") == "test" for q in filtered):
        raise ValueError("Benchmark needs at least 3 filtered questions, including one test question")

    page_by_number = {page.page: page for page in pages}
    valid_chapters = {page.metadata["chapter_number"] for page in pages}
    for question in questions:
        required = ("id", "split", "question", "expected_answer", "gold_source_id", "gold_page", "evidence_quote")
        missing = [field for field in required if not question.get(field)]
        if missing:
            raise ValueError(f"Question {question.get('id', '<no id>')} is missing {missing}")
        if question["split"] not in {"dev", "test"}:
            raise ValueError(f"Question {question['id']} has invalid split {question['split']!r}")
        if question["gold_source_id"] != manifest["source_id"]:
            raise ValueError(f"Question {question['id']} has a gold source outside this corpus")

        page = page_by_number.get(int(question["gold_page"]))
        if page is None:
            raise ValueError(
                f"Question {question['id']} gold page {question['gold_page']} "
                "is not in the ingested manifest pages"
            )
        alternatives = question.get("alternative_evidence_quotes") or []
        if isinstance(alternatives, str):
            alternatives = [alternatives]
        quotes = [question["evidence_quote"], *alternatives]
        normalized_source = " ".join(page.text.casefold().split())
        if not any(" ".join(str(q).casefold().split()) in normalized_source for q in quotes):
            raise ValueError(
                f"Question {question['id']} evidence quote was not found on printed "
                f"page {page.page}; check its gold page and quote before measuring retrieval"
            )

        filters = question.get("filter") or {}
        unsupported = set(filters) - PgVectorStore.FILTER_FIELDS
        if unsupported:
            raise ValueError(f"Question {question['id']} uses unsupported filter fields {sorted(unsupported)}")
        for key, value in filters.items():
            if key == "chapter_number" and value not in valid_chapters:
                raise ValueError(f"Question {question['id']} filters to unknown chapter {value}")
            available_values = {page.metadata.get(key) for page in pages}
            if value not in available_values:
                raise ValueError(
                    f"Question {question['id']} filters to {key}={value!r}, "
                    "which is not present in the ingested metadata"
                )


def _coherence_rows(pages, chunks_by_strategy: dict[str, list], sample_size: int = 10) -> list[dict[str, Any]]:
    """Choose fixed page-midpoint positions before looking at retrieval scores."""
    selected = sorted(pages, key=lambda page: page.page)[:sample_size]
    rows: list[dict[str, Any]] = []
    for page in selected:
        position = len(page.text) // 2
        page_id = page.metadata.get("page_id", str(page.page))
        for strategy, chunks in chunks_by_strategy.items():
            candidates = [
                chunk for chunk in chunks
                if chunk.page == page.page
                and chunk.metadata["char_start"] <= position < chunk.metadata["char_end"]
            ]
            if not candidates:
                rows.append({
                    "page_id": page_id, "printed_page": page.page,
                    "reference_char": position, "strategy": strategy,
                    "chunk_id": "", "chunk_text": "",
                    "coherence_score_0_to_2": "", "reason": "No chunk covered the selected position",
                })
            for chunk in candidates:
                rows.append({
                    "page_id": page_id, "printed_page": page.page,
                    "reference_char": position, "strategy": strategy,
                    "chunk_id": chunk.chunk_id, "chunk_text": chunk.text,
                    "coherence_score_0_to_2": "", "reason": "",
                })
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    starter_dir = project_root / "starter"
    load_dotenv(starter_dir / ".env")
    output_dir = project_root / "artifacts" / datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%S%fZ")
    output_dir.mkdir(parents=True, exist_ok=False)
    print(f"Run outputs: {output_dir}")

    manifest_path = project_root / "data" / "manifest.json"
    pdf_path = project_root / "data" / "progit.pdf"
    benchmark_path = project_root / "data" / "benchmark.json"
    manifest = _read_json(manifest_path)
    benchmark = _read_json(benchmark_path)
    pages = load_pdf(pdf_path, manifest)
    if len(pages) < 30:
        raise ValueError(f"Need at least 30 useful pages; loaded {len(pages)}")

    # Persist extraction before validation, model loading, or database work so
    # the intermediate result remains available if a later step fails.
    _write_pdf_extraction(output_dir / "pdf_extraction.txt", pages)

    chunks_a = [chunk for page in pages for chunk in fixed_size(page, size=500, overlap=50)]
    chunks_b = [chunk for page in pages for chunk in by_paragraph(page, max_size=1200)]
    if not chunks_a or not chunks_b:
        raise ValueError("Chunking produced no chunks; check extracted page text")
    chunks_by_strategy = {"A": chunks_a, "B": chunks_b, "C": chunks_b}
    _write_chunking_result(output_dir / "chunking_result.txt", chunks_by_strategy)

    # Reject stale gold pages/quotes before spending time embedding or indexing.
    _validate_benchmark(benchmark, manifest, pages)

    model_name = (os.getenv("EMBEDDING_MODEL") or "BAAI/bge-large-en-v1.5").strip()
    embedder = LocalBgeEmbedder(model_name=model_name)
    database_url = os.getenv("DATABASE_URL", "")
    store = PgVectorStore(database_url, embedder.dimensions)
    store.create_schema()

    document_ids = {page.document_id for page in pages}
    stats: dict[str, Any] = {
        "A": {"chunk_count": len(chunks_a), "chunk_chars": [len(chunk.text) for chunk in chunks_a]},
        "B": {"chunk_count": len(chunks_b), "chunk_chars": [len(chunk.text) for chunk in chunks_b]},
        "C": {"chunk_count": len(chunks_b), "chunk_chars": [len(chunk.text) for chunk in chunks_b]},
    }
    vectors_by_strategy: dict[str, list[list[float]]] = {}

    for strategy in ("A", "B"):
        chunks = chunks_by_strategy[strategy]
        started = time.perf_counter()
        vectors = embedder.embed_documents([chunk.text for chunk in chunks])
        embedding_seconds = time.perf_counter() - started
        started = time.perf_counter()
        store.upsert(strategy, chunks, vectors, document_ids=document_ids)
        database_seconds = time.perf_counter() - started
        vectors_by_strategy[strategy] = vectors
        stats[strategy].update({
            "embedding_seconds": embedding_seconds,
            "database_upsert_seconds": database_seconds,
        })

    # C uses precisely B's chunks and vectors; only the query-time filter changes.
    started = time.perf_counter()
    store.upsert("C", chunks_b, vectors_by_strategy["B"], document_ids=document_ids)
    stats["C"].update({
        "embedding_seconds": 0.0,
        "vectors_reused_from": "B",
        "database_upsert_seconds": time.perf_counter() - started,
    })

    traces: list[dict[str, Any]] = []
    manual_test_rows: list[dict[str, Any]] = []
    for question in benchmark["questions"]:
        query_vector = embedder.embed_query(question["question"])
        for strategy in ("A", "B", "C"):
            filters = (question.get("filter") or None) if strategy == "C" else None
            started = time.perf_counter()
            hits = store.search(strategy, query_vector, top_k=3, filters=filters)
            search_seconds = time.perf_counter() - started
            eval_question = {**question, "strategy": strategy}
            trace = evaluate_query(eval_question, hits)
            trace["filter_applied"] = filters
            trace["search_seconds"] = search_seconds
            traces.append(trace)

            if question["split"] == "test":
                manual_test_rows.append({
                    "question_id": question["id"],
                    "strategy": strategy,
                    "question": question["question"],
                    "filter_applied": filters,
                    "top3_hits": trace["hits"],
                    "grounding_score_0_to_2": "",
                    "metadata_correct": "",
                    "reason": "",
                })

    for strategy, details in stats.items():
        lengths = details.pop("chunk_chars")
        details.update({
            "chunk_chars_total": sum(lengths),
            "chunk_chars_min": min(lengths),
            "chunk_chars_max": max(lengths),
            "chunk_chars_mean": sum(lengths) / len(lengths),
        })

    _write_jsonl(output_dir / "retrieval_traces.jsonl", traces)
    _write_json(output_dir / "retrieval_summary.json", summarize(traces))
    _write_jsonl(output_dir / "manual_test_review.jsonl", manual_test_rows)
    _write_csv(
        output_dir / "coherence_review.csv",
        _coherence_rows(pages, chunks_by_strategy),
        ["page_id", "printed_page", "reference_char", "strategy", "chunk_id",
         "chunk_text", "coherence_score_0_to_2", "reason"],
    )
    _write_json(output_dir / "run_config.json", {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_id": manifest["source_id"],
        "pdf": str(pdf_path.relative_to(project_root)),
        "manifest": str(manifest_path.relative_to(project_root)),
        "benchmark": str(benchmark_path.relative_to(project_root)),
        "pages_ingested": len(pages),
        "model_name": embedder.model_name,
        "embedding_dimensions": embedder.dimensions,
        "device": embedder.device,
        "similarity": "cosine (pgvector exact ORDER BY cosine distance)",
        "cost_usd": 0,
        "strategies": {
            "A": "500-character fixed windows, 50-character overlap, no metadata filter",
            "B": "Paragraph grouping up to 1200 characters, no metadata filter",
            "C": "Same chunks and vectors as B; apply only benchmark-provided metadata filters",
        },
        "strategy_stats": stats,
        "outputs": [
            "pdf_extraction.txt", "chunking_result.txt",
            "retrieval_traces.jsonl", "retrieval_summary.json",
            "manual_test_review.jsonl", "coherence_review.csv",
        ],
    })

    print(f"Ingested {len(pages)} pages into {sum(s['chunk_count'] for s in stats.values())} strategy-chunks.")
    print(f"Model: {embedder.model_name} ({embedder.dimensions} dimensions; {embedder.device})")
    print(f"Results saved to: {output_dir}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        raise
