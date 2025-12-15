import os
import logging
from typing import List, Dict, Any, Optional
from ai_assistant.memory.vector_store import VectorStore
from ai_assistant.llm_interface.ollama_client import OllamaProvider
from ai_assistant.config import get_data_dir

logger = logging.getLogger(__name__)

class RAGSystem:
    """
    Manages Retrieval-Augmented Generation (RAG) capabilities.
    Integrates VectorStore with the LLM provider for embeddings.
    """
    def __init__(self, ollama_provider: OllamaProvider):
        self.ollama_provider = ollama_provider
        self.storage_path = os.path.join(get_data_dir(), "rag_vector_store.json")
        self.vector_store = VectorStore(self.storage_path)

    async def ingest_fact(self, fact_text: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        Ingests a single fact into the RAG system.
        """
        try:
            embedding = await self.ollama_provider.get_embeddings_async(fact_text)
            if not embedding:
                logger.error(f"Failed to generate embedding for fact: {fact_text[:50]}...")
                return False

            self.vector_store.add_documents([fact_text], [embedding], [metadata] if metadata else None)
            return True
        except Exception as e:
            logger.error(f"Error ingesting fact into RAG system: {e}")
            return False

    async def retrieve_context(self, query: str, k: int = 3, score_threshold: float = 0.35) -> List[Dict[str, Any]]:
        """
        Retrieves relevant context for a query.
        """
        try:
            embedding = await self.ollama_provider.get_embeddings_async(query)
            if not embedding:
                logger.error(f"Failed to generate embedding for query: {query[:50]}...")
                return []

            results = self.vector_store.search(embedding, k=k, score_threshold=score_threshold)
            return results
        except Exception as e:
            logger.error(f"Error retrieving context from RAG system: {e}")
            return []

    def delete_fact_by_id(self, fact_id: str):
        """
        Deletes a fact from the vector store by its ID.
        """
        try:
            self.vector_store.delete([fact_id])
            return True
        except Exception as e:
            logger.error(f"Error deleting fact by ID {fact_id}: {e}")
            return False

    def generate_id(self, text: str) -> str:
        """
        Exposes the ID generation logic (which is effectively in VectorStore, but convenient here).
        Actually VectorStore has _generate_id. We can use that or replicate logic.
        """
        return self.vector_store._generate_id(text)

    async def sync_existing_facts(self, all_facts: List[Dict[str, Any]]):
        """
        Syncs existing facts from MemoryManager to the VectorStore if needed.
        """
        # With Chroma, we can just upsert everything; it handles duplicates by ID.
        if all_facts:
            logger.info(f"Syncing {len(all_facts)} facts to RAG system (upsert)...")

            # To be efficient, we might want to batch this, but for now simple loop or bulk add
            # Note: We need embeddings for all of them. This might be slow if we do it one by one.
            # But get_embeddings_async is likely single-item.

            for fact in all_facts:
                text = fact.get("text")
                if text:
                    await self.ingest_fact(text, metadata=fact)
