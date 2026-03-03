import os
import logging
import hashlib
from typing import List, Dict, Any, Optional

try:
    import chromadb
    from chromadb.config import Settings
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False

logger = logging.getLogger(__name__)

class VectorStore:
    """
    A persistent vector store using ChromaDB.
    """
    def __init__(self, storage_path: str, collection_name: str = "learned_facts"):
        self.storage_path = storage_path
        self.collection_name = collection_name

        # Determine the directory for ChromaDB
        if storage_path.endswith('.json'):
            self.persist_directory = os.path.join(os.path.dirname(storage_path), "chroma_db")
        else:
            self.persist_directory = os.path.join(storage_path, "chroma_db")

        if not CHROMADB_AVAILABLE:
            logger.error("ChromaDB is not installed. VectorStore will not function correctly.")
            self.client = None
            self.collection = None
            return

        # Attempt to initialize with self-healing pattern
        try:
             self._init_chroma()
        except Exception as e:
             logger.error(f"First attempt to initialize ChromaDB failed: {e}")
             if "database is malformed" in str(e).lower() or "disk I/O error" in str(e).lower():
                 self._handle_corruption()
                 # Retry once
                 try:
                     self._init_chroma()
                 except Exception as e2:
                     logger.critical(f"FATAL: Failed to initialize ChromaDB after reset: {e2}")
                     self.client = None
                     self.collection = None
             else:
                 # Re-raise if not a corruption issue, or handle gracefully
                 logger.error(f"ChromaDB initialization failed with non-corruption error: {e}")
                 self.client = None
                 self.collection = None

    def _init_chroma(self):
        os.makedirs(self.persist_directory, exist_ok=True)
        self.client = chromadb.PersistentClient(path=self.persist_directory)

        # Get or create the collection
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"} # Use cosine similarity
        )
        logger.info(f"Initialized ChromaDB at {self.persist_directory} (Collection: {self.collection_name})")

    def _handle_corruption(self):
        import shutil
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{self.persist_directory}_corrupt_backup_{timestamp}"
        logger.warning(f"ChromaDB corruption detected. Moving '{self.persist_directory}' to '{backup_path}'...")
        try:
            if os.path.exists(self.persist_directory):
                shutil.move(self.persist_directory, backup_path)
                logger.info("Corrupt DB moved successfully. System will start with fresh DB.")
            else:
                logger.warning("Chroma dir disappeared during check?")
        except Exception as e:
            logger.critical(f"Failed to move corrupt DB: {e}.")

    def add_documents(self, texts: List[str], embeddings: List[List[float]], metadatas: Optional[List[Dict[str, Any]]] = None):
        """
        Adds documents and their embeddings to the store.
        """
        if not self.collection:
            logger.warning("ChromaDB collection is not initialized.")
            return

        if not texts or not embeddings:
            return

        if len(texts) != len(embeddings):
            raise ValueError("Number of texts and embeddings must match.")

        if metadatas and len(metadatas) != len(texts):
            raise ValueError("Number of metadata items must match texts.")

        # Generate IDs based on content hash to avoid exact duplicates
        ids = [self._generate_id(text) for text in texts]

        try:
            self.collection.upsert(
                documents=texts,
                embeddings=embeddings,
                metadatas=metadatas,
                ids=ids
            )
        except Exception as e:
            logger.error(f"Error adding documents to ChromaDB: {e}")

    def search(self, query_embedding: List[float], k: int = 5, score_threshold: float = 0.0, filter_criteria: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Searches for the k most similar documents to the query embedding.
        """
        if not self.collection:
            logger.warning("ChromaDB collection is not initialized.")
            return []

        try:
            # ChromaDB expects a list of query embeddings
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=k,
                where=filter_criteria
            )

            # ChromaDB returns lists of lists (one per query)
            # Structure:
            # results['documents'][0] -> list of texts
            # results['metadatas'][0] -> list of metadatas
            # results['distances'][0] -> list of distances

            processed_results = []

            if not results['documents']:
                return []

            documents = results['documents'][0]
            metadatas = results['metadatas'][0]
            distances = results['distances'][0]
            ids = results['ids'][0]

            for i in range(len(documents)):
                # Chroma returns distance (dissimilarity) for cosine usually,
                # but "hnsw:space": "cosine" returns cosine distance (1 - similarity).
                # However, we want similarity score.
                # Distance range for cosine is [0, 2]. 0 is identical.
                # Similarity = 1 - distance.

                distance = distances[i]
                similarity = 1 - distance

                if similarity < score_threshold:
                    continue

                processed_results.append({
                    "text": documents[i],
                    "metadata": metadatas[i] if metadatas else {},
                    "score": similarity,
                    "id": ids[i]
                })

            return processed_results

        except Exception as e:
            logger.error(f"Error searching ChromaDB: {e}")
            return []

    def get_all(self) -> List[Dict[str, Any]]:
        """
        Retrieves all documents from the collection.
        """
        if not self.collection:
            return []
            
        try:
            results = self.collection.get()
            
            processed = []
            if not results or not results.get('ids'):
                return []
                
            ids = results['ids']
            documents = results.get('documents', [])
            metadatas = results.get('metadatas', [])
            
            for i in range(len(ids)):
                processed.append({
                    "id": ids[i],
                    "text": documents[i] if documents else "",
                    "metadata": metadatas[i] if metadatas else {}
                })
            return processed
        except Exception as e:
            logger.error(f"Error getting all records from ChromaDB: {e}")
            return []

    def delete(self, ids: List[str]):
        """
        Deletes documents by ID.
        """
        if not self.collection:
            return

        try:
            self.collection.delete(ids=ids)
        except Exception as e:
            logger.error(f"Error deleting from ChromaDB: {e}")

    def _generate_id(self, text: str) -> str:
        """
        Generates a deterministic ID based on the text content.
        """
        return hashlib.md5(text.encode('utf-8')).hexdigest()
