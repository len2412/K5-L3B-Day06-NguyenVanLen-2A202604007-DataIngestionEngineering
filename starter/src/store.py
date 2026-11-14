"""Persistent PostgreSQL + pgvector storage and metadata-filtered retrieval."""
import json
import math
from typing import Any, Iterable

import psycopg
from psycopg.types.json import Jsonb

from .models import Chunk


class PgVectorStore:
    FILTER_FIELDS = {
        "chapter_number",
        "chapter_title",
        "page_id",
        "printed_page",
        "physical_pdf_page",
        "chunking_method",
    }

    def __init__(self, database_url: str, dimensions: int) -> None:
        if not database_url:
            raise ValueError("DATABASE_URL is required")
        if not isinstance(dimensions, int) or dimensions < 1:
            raise ValueError("Embedding dimensions must be a positive integer")
        self.database_url = database_url
        self.dimensions = dimensions

    def create_schema(self) -> None:
        with psycopg.connect(self.database_url) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS ingestion_chunks (
                    strategy TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    page INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    embedding vector({self.dimensions}) NOT NULL,
                    PRIMARY KEY (strategy, chunk_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS ingestion_chunks_metadata_idx
                ON ingestion_chunks USING gin (metadata)
            """)
            column_type = conn.execute("""
                SELECT format_type(attribute.atttypid, attribute.atttypmod)
                FROM pg_attribute AS attribute
                WHERE attribute.attrelid = 'ingestion_chunks'::regclass
                  AND attribute.attname = 'embedding'
                  AND NOT attribute.attisdropped
            """).fetchone()
            expected_type = f"vector({self.dimensions})"
            if not column_type or column_type[0] != expected_type:
                actual = column_type[0] if column_type else "missing"
                raise RuntimeError(
                    f"Existing pgvector column is {actual}; expected {expected_type}. "
                    "Use a fresh lab database or migrate its vectors before changing models."
                )

    def upsert(
        self,
        strategy: str,
        chunks: list[Chunk],
        vectors: list[list[float]],
        document_ids: Iterable[str] | None = None,
    ) -> None:
        """Replace a strategy's rows for the named documents, including stale chunks."""
        if strategy not in {"A", "B", "C"}:
            raise ValueError("strategy must be A, B, or C")
        if len(chunks) != len(vectors):
            raise ValueError("Each chunk must have exactly one embedding")
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
            raise ValueError("Chunk IDs must be unique within an upsert")

        for vector in vectors:
            if len(vector) != self.dimensions:
                raise ValueError(f"All vectors must have {self.dimensions} dimensions")
            if any(not math.isfinite(float(value)) for value in vector):
                raise ValueError("Embedding vectors must contain only finite numbers")

        ids = set(document_ids or ())
        ids.update(chunk.document_id for chunk in chunks)
        if any(not document_id for document_id in ids):
            raise ValueError("document_ids cannot contain empty values")

        rows = [(
            strategy, chunk.chunk_id, chunk.document_id, chunk.source, chunk.page,
            chunk.text, Jsonb(chunk.metadata),
            "[" + ",".join(str(float(value)) for value in vector) + "]",
        ) for chunk, vector in zip(chunks, vectors)]

        with psycopg.connect(self.database_url) as conn:
            for document_id in sorted(ids):
                conn.execute(
                    "DELETE FROM ingestion_chunks WHERE strategy=%s AND document_id=%s",
                    (strategy, document_id),
                )
            if rows:
                with conn.cursor() as cur:
                    cur.executemany("""
                        INSERT INTO ingestion_chunks
                            (strategy, chunk_id, document_id, source, page, content, metadata, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)
                    """, rows)

    def search(
        self,
        strategy: str,
        query_vector: list[float],
        top_k: int = 3,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if strategy not in {"A", "B", "C"}:
            raise ValueError("strategy must be A, B, or C")
        if len(query_vector) != self.dimensions:
            raise ValueError(f"Query vector must have {self.dimensions} dimensions")
        if any(not math.isfinite(float(value)) for value in query_vector):
            raise ValueError("Query vector must contain only finite numbers")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        filters = filters or {}
        unsupported = set(filters) - self.FILTER_FIELDS
        if unsupported:
            raise ValueError(f"Unsupported metadata filter fields: {sorted(unsupported)}")

        vector = "[" + ",".join(str(float(value)) for value in query_vector) + "]"
        sql = """
            SELECT chunk_id, document_id, source, page, content, metadata,
                   1 - (embedding <=> %s::vector) AS score
            FROM ingestion_chunks
            WHERE strategy=%s
        """
        params: list[Any] = [vector, strategy]
        if filters:
            sql += " AND metadata @> %s::jsonb"
            params.append(json.dumps(filters, ensure_ascii=False))
        sql += " ORDER BY embedding <=> %s::vector LIMIT %s"
        params.extend([vector, top_k])

        with psycopg.connect(self.database_url) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [{
            "chunk_id": row[0],
            "document_id": row[1],
            "source": row[2],
            "page": row[3],
            "text": row[4],
            "metadata": row[5],
            "score": float(row[6]),
        } for row in rows]
