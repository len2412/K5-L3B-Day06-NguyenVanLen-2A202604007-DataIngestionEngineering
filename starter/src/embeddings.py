"""Local Hugging Face embeddings using a cached BAAI BGE model."""
import os
from typing import Protocol

# This lab uses pre-downloaded models. Fail if the requested model is not cached
# instead of silently attempting a network download or using mock vectors.
os.environ["HF_HUB_OFFLINE"] = "1"

import torch
from sentence_transformers import SentenceTransformer


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, question: str) -> list[float]: ...


class LocalBgeEmbedder:
    """Create normalized dense vectors from a cached BGE model.

    Use the Hugging Face model ID (for example ``BAAI/bge-large-en-v1.5``),
    not the cache directory name (``models--BAAI--bge-large-en-v1.5``).
    """

    _BGE_EN_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

    def __init__(self, model_name: str = "BAAI/bge-large-en-v1.5", batch_size: int = 16) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.model_name = model_name
        self.batch_size = batch_size
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        try:
            self.model = SentenceTransformer(model_name, device=self.device)
        except Exception as exc:
            raise RuntimeError(
                f"Could not load {model_name!r} from the local Hugging Face cache. "
                "Confirm the model is downloaded and the model ID is correct."
            ) from exc

        self.dimensions = self.model.get_embedding_dimension()
        if not self.dimensions:
            raise RuntimeError(f"Model {model_name!r} did not report an embedding dimension")

    def _encode(self, texts: list[str], *, show_progress_bar: bool) -> list[list[float]]:
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("Embedding inputs must be non-empty strings")

        vectors = self.model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=show_progress_bar,
        )
        result = [[float(value) for value in vector] for vector in vectors]
        if len(result) != len(texts):
            raise RuntimeError("Embedding model returned a different number of vectors than inputs")
        if any(len(vector) != self.dimensions for vector in result):
            raise RuntimeError("Embedding model returned an unexpected vector dimension")
        return result

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed passages without adding a query instruction."""
        return self._encode(texts, show_progress_bar=bool(texts))

    def embed_query(self, question: str) -> list[float]:
        """Embed a query in the same model space as stored document vectors."""
        if not question.strip():
            raise ValueError("Question must not be empty")
        # BGE v1.5 recommends this instruction for short-query-to-passage search.
        # BGE-M3 is multilingual and does not use this v1.5-specific prefix.
        query = question.strip()
        if self.model_name.rstrip("/").endswith("bge-large-en-v1.5"):
            query = self._BGE_EN_QUERY_PREFIX + query
        return self._encode([query], show_progress_bar=False)[0]
