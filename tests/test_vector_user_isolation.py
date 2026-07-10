"""Tests for vector engine user isolation metadata and filtering."""

import numpy as np


class _FakeModel:
    def encode(self, texts):
        # deterministic tiny vectors for tests
        out = []
        for text in texts:
            out.append([float(len(text) % 7), 1.0, 0.5, 0.25])
        return np.array(out, dtype="float32")


class _FakeIndex:
    def __init__(self):
        self.vectors = []

    @property
    def ntotal(self):
        return len(self.vectors)

    def add(self, arr):
        for row in arr:
            self.vectors.append(np.array(row, dtype="float32"))

    def search(self, _arr, k):
        k = min(k, self.ntotal)
        if k <= 0:
            return np.array([[]], dtype="float32"), np.array([[]], dtype="int64")
        distances = np.array([[float(i) for i in range(k)]], dtype="float32")
        indices = np.array([[i for i in range(k)]], dtype="int64")
        return distances, indices


def test_vector_search_user_filter(isolated_db, monkeypatch):
    import aihub.vector_engine as ve

    fake_index = _FakeIndex()

    # reset globals
    ve._index = None
    ve._meta = []

    def fake_encode(text, input_type="document"):
        v = np.array(
            [float(len(text) % 7), 1.0, 0.5, 0.25], dtype="float32"
        )
        dim = int(v.size)
        trace = {
            "embedding_provider": "test",
            "embedding_model": "fake",
            "embedding_dimension": dim,
            "embedding_fallback_used": False,
            "primary_provider_attempted": "test",
            "configured_provider": "test",
            "configured_model_env": "test",
        }
        return v, dim, trace

    monkeypatch.setattr(ve, "_encode_text", fake_encode)
    monkeypatch.setattr(ve, "_init_index", lambda: fake_index)
    monkeypatch.setattr(ve, "_save", lambda: None)

    ve.add_memory("sekret user-a", user_id="user-a")
    ve.add_memory("sekret user-b", user_id="user-b")

    res_a = ve.search("sekret", k=5, user_id="user-a")
    res_b = ve.search("sekret", k=5, user_id="user-b")

    texts_a = [r["text"] for r in res_a.get("results", [])]
    texts_b = [r["text"] for r in res_b.get("results", [])]

    assert "sekret user-a" in texts_a
    assert "sekret user-b" not in texts_a

    assert "sekret user-b" in texts_b
    assert "sekret user-a" not in texts_b

    assert "ts" in ve._meta[0]
    assert "ts" in ve._meta[1]
