"""Local text embeddings for semantic memory recall.

Keyword search can't connect "my kid" to a memory about "my daughter". A small embedding model running on
this machine can, with no API calls and no cost. It's optional: ``pip install fastembed`` (ONNX, ~70 MB model
downloaded once into weebo_data/cache). Without it, recall stays keyword-only.
"""

from __future__ import annotations

import array
import importlib.util
import math
from typing import Protocol, Sequence

from .. import paths

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


class Embedder(Protocol):
    name: str

    def warm(self) -> None: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def available() -> bool:
    return importlib.util.find_spec("fastembed") is not None


class FastEmbedder:
    """fastembed's ONNX models: CPU only, a few milliseconds per short text."""

    def __init__(self, model: str = DEFAULT_MODEL):
        self.name = model
        self._model = None

    def warm(self) -> None:
        if self._model is None:
            from fastembed import TextEmbedding
            self._model = TextEmbedding(model_name=self.name, cache_dir=str(paths.cache_dir() / "embeddings"))

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.warm()
        return [normalize([float(x) for x in vector]) for vector in self._model.embed(list(texts))]


def normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def pack(vector: Sequence[float]) -> bytes:
    return array.array("f", vector).tobytes()


def unpack(blob: bytes) -> list[float]:
    values = array.array("f")
    values.frombytes(blob)
    return values.tolist()


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
