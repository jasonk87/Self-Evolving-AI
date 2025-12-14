from typing import List, Dict, Any, Optional
import datetime
import uuid
import logging
import asyncio # Added for async operations
from ai_assistant.memory.persistent_memory import (
    load_learned_facts, save_learned_facts,
    load_actionable_insights, save_actionable_insights
)
# Integration with RAG
from ai_assistant.memory.rag_system import RAGSystem
from ai_assistant.llm_interface.ollama_client import OllamaProvider

logger = logging.getLogger(__name__)

class MemoryManager:
    """
    Manages the AI's long-term memory (facts and insights).
    """

    def __init__(self):
        self.rag_system: Optional[RAGSystem] = None
        self._initialize_rag()

    def _initialize_rag(self):
        try:
            provider = OllamaProvider()
            self.rag_system = RAGSystem(provider)
            # Potentially sync existing facts asynchronously
            # Since __init__ is sync, we can't await here.
            # Ideally, this should be done in a startup service or lazy loaded.
        except Exception as e:
            logger.error(f"Failed to initialize RAG system: {e}")

    async def retrieve_relevant_context(self, query: str, k: int = 3) -> List[Dict[str, Any]]:
        """
        Retrieves relevant context (facts) for a given query using RAG.
        """
        if not self.rag_system:
             return []

        return await self.rag_system.retrieve_context(query, k=k)

    async def add_fact_with_rag(self, text: str, category: str = "manual", source: str = "user_interface") -> Dict[str, Any]:
        """
        Adds a fact and indexes it in the RAG system.
        Note: This is an async version of add_fact.
        """
        new_fact = self.add_fact(text, category, source)

        if self.rag_system:
            await self.rag_system.ingest_fact(new_fact['text'], metadata=new_fact)

        return new_fact

    # --- Facts Management ---

    def get_all_memories_structured(self) -> Dict[str, Any]:
        """
        Returns a structured dictionary of memories for visualization.
        """
        facts = load_learned_facts()
        insights = load_actionable_insights()

        return {
            "facts": facts,
            "insights": insights
        }

    def get_all_facts(self) -> List[Dict[str, Any]]:
        """Returns all learned facts."""
        return load_learned_facts()

    def add_fact(self, text: str, category: str = "manual", source: str = "user_interface") -> Dict[str, Any]:
        """
        Adds a new manually created fact.
        """
        facts = load_learned_facts()

        new_fact = {
            "fact_id": f"fact_{uuid.uuid4().hex[:8]}",
            "text": text,
            "category": category,
            "source": source,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }

        facts.append(new_fact)
        if save_learned_facts(facts):
            logger.info(f"Added new fact: {new_fact['fact_id']}")

            # Fire-and-forget async RAG ingestion if loop is running
            if self.rag_system:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self.rag_system.ingest_fact(new_fact['text'], metadata=new_fact))
                except RuntimeError:
                    # No running loop, skipping async ingest (sync fallback not implemented here to keep add_fact sync)
                    logger.warning(f"Could not ingest fact '{new_fact['fact_id']}' into RAG: no active event loop.")

            return new_fact
        else:
            logger.error("Failed to save new fact.")
            raise Exception("Failed to save new fact.")

    def update_fact(self, fact_id: str, text: str) -> Optional[Dict[str, Any]]:
        """
        Updates an existing fact's text.
        """
        facts = load_learned_facts()
        fact_found = False
        updated_fact = None

        for fact in facts:
            if fact.get("fact_id") == fact_id:
                fact["text"] = text
                fact["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                fact_found = True
                updated_fact = fact
                break

        if fact_found:
            if save_learned_facts(facts):
                logger.info(f"Updated fact: {fact_id}")
                return updated_fact
            else:
                logger.error(f"Failed to save updated fact: {fact_id}")
                raise Exception("Failed to save updated fact.")
        else:
            logger.warning(f"Fact not found for update: {fact_id}")
            return None

    def delete_fact(self, fact_id: str) -> bool:
        """
        Deletes a fact by ID.
        """
        facts = load_learned_facts()
        original_count = len(facts)
        facts = [f for f in facts if f.get("fact_id") != fact_id]

        if len(facts) < original_count:
            if save_learned_facts(facts):
                logger.info(f"Deleted fact: {fact_id}")
                return True
            else:
                logger.error(f"Failed to save facts after deletion of: {fact_id}")
                raise Exception("Failed to save facts after deletion.")
        else:
            logger.warning(f"Fact not found for deletion: {fact_id}")
            return False

    # --- Insights Management ---

    def get_all_insights(self) -> List[Dict[str, Any]]:
        """Returns all actionable insights."""
        return load_actionable_insights()

    async def ingest_insight_to_rag(self, insight: Dict[str, Any]):
        """
        Ingests a specific insight into RAG if it is relevant for context (e.g. USER_PREFERENCE).
        """
        if not self.rag_system:
            return

        insight_type = insight.get("type")
        if insight_type in ["USER_PREFERENCE_LEARNED", "USER_FRUSTRATION", "KNOWLEDGE_GAP_IDENTIFIED"]:
             text = f"Insight ({insight_type}): {insight.get('description')}"
             await self.rag_system.ingest_fact(text, metadata={"source": "insight", "id": insight.get("insight_id"), "type": insight_type})
             logger.info(f"Ingested insight {insight.get('insight_id')} into RAG.")

    def update_insight_status(self, insight_id: str, status: str) -> Optional[Dict[str, Any]]:
        """
        Updates the status of an insight (e.g., 'DISMISSED', 'APPROVED').
        """
        insights = load_actionable_insights()
        insight_found = False
        updated_insight = None

        for insight in insights:
            if insight.get("insight_id") == insight_id:
                insight["status"] = status
                # Update metadata if needed
                if "metadata" not in insight:
                    insight["metadata"] = {}
                insight["metadata"][f"status_change_{status.lower()}_timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

                insight_found = True
                updated_insight = insight
                break

        if insight_found:
            if save_actionable_insights(insights):
                logger.info(f"Updated insight status: {insight_id} -> {status}")
                
                # If approved/acknowledged, ensure it's in RAG (fire and forget if loop exists)
                if status in ["APPROVED", "PENDING", "NEW"]: # "NEW" is default usually
                     try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(self.ingest_insight_to_rag(updated_insight))
                     except RuntimeError:
                        pass

                return updated_insight
            else:
                logger.error(f"Failed to save updated insight: {insight_id}")
                raise Exception("Failed to save updated insight.")
        else:
            logger.warning(f"Insight not found for update: {insight_id}")
            return None

    def delete_insight(self, insight_id: str) -> bool:
        """
        Deletes an insight by ID.
        """
        insights = load_actionable_insights()
        original_count = len(insights)
        insights = [i for i in insights if i.get("insight_id") != insight_id]

        if len(insights) < original_count:
            if save_actionable_insights(insights):
                logger.info(f"Deleted insight: {insight_id}")
                return True
            else:
                logger.error(f"Failed to save insights after deletion of: {insight_id}")
                raise Exception("Failed to save insights after deletion.")
        else:
            logger.warning(f"Insight not found for deletion: {insight_id}")
            return False
