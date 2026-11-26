"""Snapshot-scoped BM25, character TF-IDF and reciprocal rank fusion."""

from collections import OrderedDict
import re
import threading
from typing import Literal

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from .store import Store

STOP_WORDS = frozenset(
    "a an the is are was were to of in on by with for and or how what which when why does do can should would could i my me about explain compare versus vs between from this that it its as be at into their under than".split()
)
METHODS = {"bm25", "tfidf", "hybrid"}
RetrievalMethod = Literal["bm25", "tfidf", "hybrid"]


def terms(text: str) -> list[str]:
    return list(
        dict.fromkeys(
            word
            for word in re.findall(r"[a-z0-9]+", text.lower())
            if len(word) >= 2 and word not in STOP_WORDS
        )
    )[:40]


class Retriever:
    def __init__(self, store: Store):
        self.store = store
        self._cache = OrderedDict()
        self._lock = threading.Lock()

    def _index(self, snapshot):
        key = tuple(sorted(snapshot))
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            chunks = self.store.chunks(snapshot)
            vectorizer = TfidfVectorizer(
                analyzer="char_wb",
                ngram_range=(3, 5),
                max_features=40000,
                sublinear_tf=True,
                dtype=np.float32,
            )
            # Heading context helps a short paragraph retain its subject.
            matrix = vectorizer.fit_transform(
                [c["title"] + " " + c["heading"] + " " + c["body"] for c in chunks]
            )
            result = chunks, vectorizer, matrix
            self._cache[key] = result
            if len(self._cache) > 4:
                self._cache.popitem(last=False)
            return result

    def search(self, query: str, *, snapshot=None, method="hybrid", limit=6):
        if method not in METHODS or not 1 <= limit <= 12:
            raise ValueError("Use bm25, tfidf or hybrid and a limit in [1,12]")
        snapshot = self.store.snapshot() if snapshot is None else snapshot
        if not snapshot or not terms(query):
            return []
        chunks, vectorizer, matrix = self._index(snapshot)
        by_id = {chunk["id"]: chunk for chunk in chunks}
        expression = " OR ".join('"' + word + '"' for word in terms(query))
        bm25 = self.store.fts(expression, snapshot, 30) if method != "tfidf" else []
        cosine = {}
        if method != "bm25":
            scores = (matrix @ vectorizer.transform([query]).T).toarray().ravel()
            indices = sorted(
                range(len(chunks)), key=lambda i: (-float(scores[i]), chunks[i]["id"])
            )[:30]
            cosine = {
                chunks[i]["id"]: float(scores[i]) for i in indices if scores[i] >= 0.025
            }
        vector_ids = list(cosine)
        rankings = (
            [bm25]
            if method == "bm25"
            else [vector_ids]
            if method == "tfidf"
            else [bm25, vector_ids]
        )
        fused = {}
        for ranking in rankings:
            for rank, chunk_id in enumerate(ranking, 1):
                fused[chunk_id] = fused.get(chunk_id, 0) + 1 / (60 + rank)
        ordered = sorted(fused, key=lambda c: (-fused[c], c))[:limit]
        current = set(self.store.snapshot())
        return [
            {
                **by_id[key],
                "is_current": by_id[key]["version_id"] in current,
                "score": fused[key],
                "cosine": cosine.get(key),
                "bm25_rank": bm25.index(key) + 1 if key in bm25 else None,
                "vector_rank": vector_ids.index(key) + 1 if key in vector_ids else None,
            }
            for key in ordered
        ]
