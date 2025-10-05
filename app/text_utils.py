from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from collections import Counter
from collections import Counter
from typing import Iterable, List, Sequence

import re
import unicodedata

_WORD_SEP = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_PAIR_REORDER = {
    ("small", "box"): ("box", "1.5"),
    ("medium", "box"): ("box", "3.0"),
    ("large", "box"): ("box", "4.5"),
    ("extra", "large", "box"): ("box", "6.0"),
    ("xl", "box"): ("box", "6.0"),
}


def _ascii_fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _is_numeric(token: str) -> bool:
    if not token:
        return False
    if token.isdigit():
        return True
    try:
        float(token)
    except ValueError:
        return False
    return True


def singularize(token: str) -> str:
    if not token or _is_numeric(token):
        return token
    if token.endswith("ies") and len(token) > 3:
        return token[:-3] + "y"
    if token.endswith("ves") and len(token) > 3:
        return token[:-3] + "f"
    if token.endswith("ses") and len(token) > 3:
        return token[:-2]
    if token.endswith("xes") and len(token) > 3:
        return token[:-2]
    if token.endswith("s") and len(token) > 3:
        return token[:-1]
    return token


def normalize_label(raw: str) -> str:
    if raw is None:
        return ""
    working = _ascii_fold(raw.lower().strip())
    working = working.replace("_", " ").replace("-", " ")
    working = _NON_ALNUM.sub(" ", working)
    tokens = [token for token in _WORD_SEP.split(working) if token]
    if not tokens:
        return ""
    tokens = [singularize(token) for token in tokens]
    if len(tokens) in {2, 3}:
        key = tuple(tokens)
        replacement = _PAIR_REORDER.get(key)
        if replacement:
            tokens = list(replacement)
        elif tokens[-1] == "box" and tokens[0] != "box":
            tokens = ["box", *tokens[:-1]]
    return " ".join(tokens)


def generate_tokens(normalized: str) -> List[str]:
    tokens = normalized.split()
    bigrams = [" ".join(tokens[idx : idx + 2]) for idx in range(len(tokens) - 1)]
    return tokens + bigrams


def trigram_vector(normalized: str) -> Counter[str]:
    padded = f" {normalized} " if normalized else ""
    if len(padded) < 3:
        return Counter({padded: 1}) if padded else Counter()
    return Counter(padded[idx : idx + 3] for idx in range(len(padded) - 2))


def cosine_similarity(vec_a: dict[str, int], vec_b: dict[str, int]) -> float:
    if not vec_a or not vec_b:
        return 0.0
    intersection = set(vec_a) & set(vec_b)
    if not intersection:
        return 0.0
    dot = sum(vec_a[key] * vec_b[key] for key in intersection)
    if not dot:
        return 0.0
    norm_a = sum(value * value for value in vec_a.values()) ** 0.5
    norm_b = sum(value * value for value in vec_b.values()) ** 0.5
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


def stable_sort(values: Iterable[str]) -> List[str]:
    return sorted(values)


def tokenize(normalized: str) -> Sequence[str]:
    return normalized.split()
