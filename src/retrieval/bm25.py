"""Deterministic, dependency-light Okapi BM25 retrieval for Phase 7."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
import re
from time import perf_counter
from typing import Any, Literal, Sequence

import numpy as np


DEFAULT_K1 = 1.2
DEFAULT_B = 0.75
TOKENIZER_VERSION = "script_aware_unicode_alnum_casefold_v1"
TOKEN_PATTERN = re.compile(
    r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?|[가-힣]+|[^\W\d_A-Za-z가-힣]+",
    flags=re.UNICODE,
)


def tokenize(text: str) -> list[str]:
    """Case-fold Unicode alphanumeric tokens without stemming or stopword removal."""

    if not isinstance(text, str):
        raise TypeError("BM25 input must be text")
    return [match.group(0).casefold() for match in TOKEN_PATTERN.finditer(text)]


@dataclass(frozen=True)
class BM25Hit:
    chunk_id: str
    score: float


@dataclass(frozen=True)
class BM25SearchResponse:
    hits: list[BM25Hit]
    latency_ms: float
    query_token_count: int
    matched_query_terms: list[str]


class BM25Index:
    """In-memory inverted index with a pre-registered Okapi BM25 formula."""

    def __init__(
        self,
        documents: Sequence[dict[str, Any]],
        *,
        k1: float = DEFAULT_K1,
        b: float = DEFAULT_B,
    ) -> None:
        if not documents:
            raise ValueError("BM25 requires at least one document")
        if k1 <= 0:
            raise ValueError("k1 must be positive")
        if not 0 <= b <= 1:
            raise ValueError("b must be between zero and one")
        chunk_ids = [str(document["chunk_id"]) for document in documents]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("BM25 chunk IDs must be unique")

        started = perf_counter()
        tokenized = [tokenize(str(document["text"])) for document in documents]
        if any(not tokens for tokens in tokenized):
            raise ValueError("Every BM25 document must contain at least one token")
        self.chunk_ids = chunk_ids
        self.k1 = float(k1)
        self.b = float(b)
        self.document_count = len(documents)
        self.document_lengths = np.asarray(
            [len(tokens) for tokens in tokenized], dtype=np.float64
        )
        self.average_document_length = float(self.document_lengths.mean())

        postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        document_frequency: Counter[str] = Counter()
        for document_index, tokens in enumerate(tokenized):
            frequencies = Counter(tokens)
            for term, frequency in frequencies.items():
                postings[term].append((document_index, frequency))
                document_frequency[term] += 1
        self._postings = dict(postings)
        self._idf = {
            term: math.log(
                1.0
                + (self.document_count - frequency + 0.5) / (frequency + 0.5)
            )
            for term, frequency in document_frequency.items()
        }
        token_digest = hashlib.sha256()
        for chunk_id, tokens in zip(chunk_ids, tokenized, strict=True):
            token_digest.update(
                json.dumps(
                    [chunk_id, tokens],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            token_digest.update(b"\n")
        order_digest = hashlib.sha256(("\n".join(chunk_ids) + "\n").encode("utf-8"))
        self.metadata = {
            "implementation": "project_native_okapi_bm25_v1",
            "formula": (
                "sum(idf(t) * tf(t,d)*(k1+1) / "
                "(tf(t,d)+k1*(1-b+b*dl/avgdl)))"
            ),
            "idf_formula": "ln(1 + (N - df + 0.5) / (df + 0.5))",
            "query_term_frequency": "multiply each term contribution by query count",
            "k1": self.k1,
            "b": self.b,
            "tokenizer_version": TOKENIZER_VERSION,
            "tokenizer": (
                "Unicode alphanumeric tokens with Latin/Hangul script boundaries; "
                "casefold; Latin apostrophes retained; hyphens split; no stemming; "
                "no stopword removal"
            ),
            "document_count": self.document_count,
            "vocabulary_size": len(self._postings),
            "total_token_count": int(self.document_lengths.sum()),
            "average_document_length": self.average_document_length,
            "minimum_document_length": int(self.document_lengths.min()),
            "maximum_document_length": int(self.document_lengths.max()),
            "document_order_sha256": order_digest.hexdigest(),
            "tokenized_corpus_sha256": token_digest.hexdigest(),
            "build_latency_ms": (perf_counter() - started) * 1000.0,
        }

    def search(
        self, query: str, *, top_k: int = 10,
        score_policy: Literal['retain_zero', 'positive_only'] = 'retain_zero',
    ) -> BM25SearchResponse:
        if top_k <= 0 or top_k > self.document_count:
            raise ValueError("top_k must be between one and the document count")
        if score_policy not in ('retain_zero', 'positive_only'):
            raise ValueError('Unsupported BM25 score policy')
        started = perf_counter()
        query_tokens = tokenize(query)
        query_frequencies = Counter(query_tokens)
        scores = np.zeros(self.document_count, dtype=np.float64)
        matched_terms = sorted(term for term in query_frequencies if term in self._postings)
        for term in matched_terms:
            idf = self._idf[term]
            query_count = query_frequencies[term]
            for document_index, term_frequency in self._postings[term]:
                length_normalization = self.k1 * (
                    1.0
                    - self.b
                    + self.b
                    * self.document_lengths[document_index]
                    / self.average_document_length
                )
                scores[document_index] += (
                    query_count
                    * idf
                    * term_frequency
                    * (self.k1 + 1.0)
                    / (term_frequency + length_normalization)
                )

        ranked_indices = sorted(
            (index for index in range(self.document_count)
             if score_policy == 'retain_zero' or scores[index] > 0),
            key=lambda index: (-float(scores[index]), self.chunk_ids[index]),
        )[:top_k]
        hits = [
            BM25Hit(
                chunk_id=self.chunk_ids[document_index],
                score=float(scores[document_index]),
            )
            for document_index in ranked_indices
        ]
        return BM25SearchResponse(
            hits=hits,
            latency_ms=(perf_counter() - started) * 1000.0,
            query_token_count=len(query_tokens),
            matched_query_terms=matched_terms,
        )
