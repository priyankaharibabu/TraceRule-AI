"""Small, deterministic BM25 retrieval over passages from one uploaded PDF."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

TOKEN_PATTERN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
DEFAULT_FOCUS = "shall must must not may not required prohibited required to"


def tokenize(text: str) -> list[str]:
    return [token.casefold() for token in TOKEN_PATTERN.findall(text)]


def retrieve_passages(
    passages: list[dict[str, Any]],
    query: str = "",
    *,
    limit: int = 5,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[dict[str, Any]]:
    """Return deterministic BM25-ranked source passages with their page metadata."""
    if not passages or limit < 1:
        return []
    query_terms = tokenize(query.strip() or DEFAULT_FOCUS)
    if not query_terms:
        return []

    tokenized = [tokenize(str(item.get("passage_text", ""))) for item in passages]
    lengths = [len(tokens) for tokens in tokenized]
    average_length = sum(lengths) / max(1, len(lengths))
    document_frequency: Counter[str] = Counter()
    frequencies = [Counter(tokens) for tokens in tokenized]
    for counts in frequencies:
        document_frequency.update(counts.keys())

    ranked: list[tuple[float, int]] = []
    total = len(passages)
    for index, counts in enumerate(frequencies):
        score = 0.0
        length_norm = 1 - b + b * lengths[index] / max(average_length, 1.0)
        for term in query_terms:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            df = document_frequency[term]
            inverse_frequency = math.log(1 + (total - df + 0.5) / (df + 0.5))
            score += inverse_frequency * (frequency * (k1 + 1)) / (frequency + k1 * length_norm)
        if score > 0:
            ranked.append((score, index))

    ranked.sort(key=lambda pair: (-pair[0], pair[1]))
    result = []
    for score, index in ranked[:limit]:
        result.append({**passages[index], "retrieval_score": round(score, 6)})
    return result
