"""Automatic evidence retrieval metrics; human labels remain separate."""
import re
import unicodedata
from collections import defaultdict
from typing import Any


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", value).strip()


def _gold_evidence(question: dict[str, Any]) -> list[str]:
    quotes: list[str] = []
    primary = question.get("evidence_quote")
    if isinstance(primary, str) and primary.strip():
        quotes.append(primary)
    alternatives = question.get("alternative_evidence_quotes") or []
    if isinstance(alternatives, str):
        alternatives = [alternatives]
    for quote in alternatives:
        if isinstance(quote, str) and quote.strip():
            quotes.append(quote)
    if not quotes:
        raise ValueError(f"Question {question.get('id')} has no evidence quote")
    return [_normalize(quote) for quote in quotes]


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    """Mark hits only when source/page and sufficient quoted evidence match.

    This is an evidence-retrieval proxy. It does not assign human grounding or
    chunk-coherence labels.
    """
    question_id = question.get("id")
    gold_source = question.get("gold_source_id")
    gold_pages = question.get("gold_pages")
    if gold_pages is None:
        if "gold_page" not in question:
            raise ValueError(f"Question {question_id} has no gold_page")
        gold_pages = [question["gold_page"]]
    elif not isinstance(gold_pages, list):
        raise ValueError(f"Question {question_id} gold_pages must be a list")

    quotes = _gold_evidence(question)
    annotated_hits = []
    for hit in hits[:3]:
        source_page_match = (
            hit.get("document_id") == gold_source
            and hit.get("page") in gold_pages
        )
        normalized_text = _normalize(hit.get("text", ""))
        evidence_match = any(quote in normalized_text for quote in quotes)
        annotated_hits.append({
            **hit,
            "gold_source_page_match": source_page_match,
            "gold_evidence_match": evidence_match,
            "evidence_hit": source_page_match and evidence_match,
        })

    hit_at_1 = bool(annotated_hits and annotated_hits[0]["evidence_hit"])
    evidence_hit_at_3 = any(hit["evidence_hit"] for hit in annotated_hits)
    return {
        "question_id": question_id,
        "split": question.get("split"),
        "strategy": question.get("strategy"),
        "hit_at_1": hit_at_1,
        "evidence_hit_at_3": evidence_hit_at_3,
        "gold_source_id": gold_source,
        "gold_pages": gold_pages,
        "evidence_quotes": [*_gold_evidence_raw(question)],
        "question": question.get("question"),
        "expected_answer": question.get("expected_answer"),
        "hits": annotated_hits,
    }


def _gold_evidence_raw(question: dict[str, Any]) -> list[str]:
    primary = question.get("evidence_quote")
    alternatives = question.get("alternative_evidence_quotes") or []
    if isinstance(alternatives, str):
        alternatives = [alternatives]
    return ([primary] if isinstance(primary, str) and primary.strip() else []) + [
        quote for quote in alternatives if isinstance(quote, str) and quote.strip()
    ]


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate Hit@1 and Evidence Hit@3 counts by strategy and split."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for trace in traces:
        grouped[(trace["strategy"], trace["split"])].append(trace)

    summary: dict[str, Any] = {}
    for (strategy, split), rows in sorted(grouped.items()):
        count = len(rows)
        hit1 = sum(bool(row["hit_at_1"]) for row in rows)
        hit3 = sum(bool(row["evidence_hit_at_3"]) for row in rows)
        summary.setdefault(strategy, {})[split] = {
            "questions": count,
            "hit_at_1": {"hits": hit1, "total": count, "rate": hit1 / count if count else 0.0},
            "evidence_hit_at_3": {"hits": hit3, "total": count, "rate": hit3 / count if count else 0.0},
        }
    return summary
