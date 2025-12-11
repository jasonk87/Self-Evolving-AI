import numpy as np
import pickle
import os
import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class VectorStore:
    """
    A simple file-based vector store using NumPy for cosine similarity.
    """
    def __init__(self, storage_path: str):
        self.storage_path = storage_path
        self.vectors: List[np.ndarray] = []
        self.documents: List[str] = []
        self.metadata: List[Dict[str, Any]] = []
        self._load()

    def add_documents(self, texts: List[str], embeddings: List[List[float]], metadatas: Optional[List[Dict[str, Any]]] = None):
        """
        Adds documents and their embeddings to the store.
        """
        if not texts or not embeddings:
            return

        if len(texts) != len(embeddings):
            raise ValueError("Number of texts and embeddings must match.")

        if metadatas and len(metadatas) != len(texts):
            raise ValueError("Number of metadata items must match texts.")

        new_vectors = [np.array(e, dtype=np.float32) for e in embeddings]

        # Normalize vectors for cosine similarity
        new_vectors = [v / np.linalg.norm(v) if np.linalg.norm(v) > 0 else v for v in new_vectors]

        self.vectors.extend(new_vectors)
        self.documents.extend(texts)
        if metadatas:
            self.metadata.extend(metadatas)
        else:
            self.metadata.extend([{} for _ in texts])

        self._save()

    def search(self, query_embedding: List[float], k: int = 5, score_threshold: float = 0.0) -> List[Dict[str, Any]]:
        """
        Searches for the k most similar documents to the query embedding.
        """
        if not self.vectors:
            return []

        query_vec = np.array(query_embedding, dtype=np.float32)
        norm = np.linalg.norm(query_vec)
        if norm > 0:
            query_vec = query_vec / norm

        # Compute cosine similarity
        # Stack vectors into a matrix
        matrix = np.stack(self.vectors)
        scores = np.dot(matrix, query_vec)

        # Get top k indices
        top_k_indices = np.argsort(scores)[::-1][:k]

        results = []
        for idx in top_k_indices:
            score = float(scores[idx])
            if score < score_threshold:
                continue

            results.append({
                "text": self.documents[idx],
                "metadata": self.metadata[idx],
                "score": score
            })

        return results

    def _save(self):
        """
        Saves the store to disk.
        """
        data = {
            "vectors": self.vectors,
            "documents": self.documents,
            "metadata": self.metadata
        }
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        with open(self.storage_path, "wb") as f:
            pickle.dump(data, f)

    def _load(self):
        """
        Loads the store from disk.
        """
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "rb") as f:
                    data = pickle.load(f)
                self.vectors = data.get("vectors", [])
                self.documents = data.get("documents", [])
                self.metadata = data.get("metadata", [])
            except Exception as e:
                logger.error(f"Failed to load vector store from {self.storage_path}: {e}")
                # Initialize empty if load fails
                self.vectors = []
                self.documents = []
                self.metadata = []
