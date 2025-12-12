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

    async def sync_existing_facts(self, all_facts: List[Dict[str, Any]]):
        """
        Syncs existing facts from MemoryManager to the VectorStore if needed.
        (This is a basic implementation; ideally, we track which facts are already indexed).
        """
        # For simplicity, we can check if the store is empty.
        # A more robust solution would check IDs.
        if not self.vector_store.documents and all_facts:
            logger.info(f"Syncing {len(all_facts)} existing facts to RAG system...")
            for fact in all_facts:
                text = fact.get("text")
                if text:
                    await self.ingest_fact(text, metadata=fact)
