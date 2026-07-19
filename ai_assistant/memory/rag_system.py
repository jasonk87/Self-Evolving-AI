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
        import asyncio
        if all_facts:
            logger.info(f"Syncing {len(all_facts)} facts to RAG system (upsert)...")

            async def _process_batch(batch):
                tasks = [self.ingest_fact(fact.get("text"), metadata=fact) for fact in batch if fact.get("text")]
                if tasks:
                    await asyncio.gather(*tasks)

            # Process in batches of 15 to avoid overloading the LLM
            batch_size = 15
            for i in range(0, len(all_facts), batch_size):
                batch = all_facts[i:i + batch_size]
                await _process_batch(batch)
                if i + batch_size < len(all_facts):
                    # Slight delay between batches to respect rate limits if needed
                    await asyncio.sleep(0.5)

    async def reconcile_facts(self, canonical_facts: List[Dict[str, Any]]) -> Dict[str, int]:
        """Make the learned-facts collection match the canonical JSON store."""
        normalized = [
            fact for fact in (canonical_facts or [])
            if isinstance(fact, dict) and str(fact.get("text") or "").strip()
        ]
        canonical_by_id = {
            self.generate_id(str(fact["text"]).strip()): fact
            for fact in normalized
        }
        indexed = self.vector_store.get_all()
        indexed_ids = {str(item.get("id")) for item in indexed if item.get("id")}
        stale_ids = sorted(indexed_ids - set(canonical_by_id))
        missing_ids = set(canonical_by_id) - indexed_ids

        if stale_ids:
            self.vector_store.delete(stale_ids)
        missing_facts = [canonical_by_id[fact_id] for fact_id in sorted(missing_ids)]
        if missing_facts:
            await self.sync_existing_facts(missing_facts)

        logger.info(
            "RAG reconciliation complete: %s added, %s stale removed.",
            len(missing_facts),
            len(stale_ids),
        )
        return {"added": len(missing_facts), "removed": len(stale_ids)}
