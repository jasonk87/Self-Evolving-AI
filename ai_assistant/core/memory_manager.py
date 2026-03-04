from typing import List, Dict, Any, Optional
import datetime
import uuid
import logging
import asyncio # Added for async operations
from ai_assistant.memory.persistent_memory import (
    load_learned_facts, save_learned_facts,
    load_actionable_insights, save_actionable_insights,
    load_episodic_memories, save_episodic_memories
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

    async def add_fact_with_rag(self, text: str, category: str = "manual", source: str = "user_interface", permanence: str = "permanent") -> Dict[str, Any]:
        """
        Adds a fact and indexes it in the RAG system.
        Note: This is an async version of add_fact.
        """
        # Pass permanence to the synchronous add_fact
        new_fact = self.add_fact(text, category, source, permanence)

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
        episodes = load_episodic_memories()

        return {
            "facts": facts,
            "insights": insights,
            "episodes": episodes
        }

    def _parse_timestamp(self, timestamp: Optional[str]) -> Optional[datetime.datetime]:
        """Parses an ISO timestamp safely and returns a timezone-aware datetime when possible."""
        if not timestamp or not isinstance(timestamp, str):
            return None

        try:
            parsed = datetime.datetime.fromisoformat(timestamp)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            return parsed
        except (TypeError, ValueError):
            return None

    def get_cortex_activity_summary(
        self,
        lookback_hours: int = 24,
        limit: int = 10,
        kinds: Optional[List[str]] = None,
        source: Optional[str] = None,
        permanence: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Builds a compact summary of recent memory-cortex activity."""
        selected_kinds = set(kinds or ["facts", "insights", "episodes"])

        facts = load_learned_facts() if "facts" in selected_kinds else []
        insights = load_actionable_insights() if "insights" in selected_kinds else []
        episodes = load_episodic_memories() if "episodes" in selected_kinds else []

        if source:
            facts = [fact for fact in facts if fact.get("source") == source]

        if permanence:
            facts = [fact for fact in facts if fact.get("permanence") == permanence]

        now = datetime.datetime.now(datetime.timezone.utc)
        cutoff = now - datetime.timedelta(hours=lookback_hours)

        recent_additions = {"facts": 0, "insights": 0, "episodes": 0}
        recent_updates = {"facts": 0, "insights": 0, "episodes": 0}
        latest_changes = []

        def process_items(items, bucket_name, id_key, label_fn):
            for item in items:
                created_at = self._parse_timestamp(item.get("created_at"))
                updated_at = self._parse_timestamp(item.get("updated_at"))

                if created_at and created_at >= cutoff:
                    recent_additions[bucket_name] += 1

                changed_at = updated_at or created_at
                if changed_at and changed_at >= cutoff:
                    is_updated = bool(updated_at and created_at and updated_at > created_at)
                    if is_updated:
                        recent_updates[bucket_name] += 1

                    latest_changes.append({
                        "kind": bucket_name[:-1],
                        "id": item.get(id_key),
                        "label": label_fn(item),
                        "changed_at": changed_at.isoformat(),
                        "change_type": "updated" if is_updated else "created",
                    })

        process_items(facts, "facts", "fact_id", lambda x: x.get("text", "")[:80])
        process_items(insights, "insights", "insight_id", lambda x: x.get("description", "")[:80])
        process_items(episodes, "episodes", "episode_id", lambda x: x.get("title", "")[:80])

        latest_changes.sort(key=lambda item: item["changed_at"], reverse=True)

        total_recent_changes = sum(recent_additions.values()) + sum(recent_updates.values())
        anomalies = []
        if total_recent_changes >= 25:
            anomalies.append({
                "level": "warning",
                "metric": "recent_change_volume",
                "message": f"High memory churn detected ({total_recent_changes} changes in the lookback window).",
            })
        if total_recent_changes == 0 and (len(facts) + len(insights) + len(episodes)) > 0:
            anomalies.append({
                "level": "info",
                "metric": "recent_change_volume",
                "message": "No recent memory mutations detected in the selected window.",
            })

        return {
            "lookback_hours": lookback_hours,
            "generated_at": now.isoformat(),
            "filters": {
                "kinds": sorted(selected_kinds),
                "source": source,
                "permanence": permanence,
            },
            "totals": {
                "facts": len(facts),
                "insights": len(insights),
                "episodes": len(episodes),
            },
            "recent": {
                "added": recent_additions,
                "updated": recent_updates,
            },
            "total_recent_changes": total_recent_changes,
            "anomalies": anomalies,
            "latest_changes": latest_changes[:limit],
        }

    # --- Episodic Memory Management ---

    def get_all_episodes(self) -> List[Dict[str, Any]]:
        """Returns all episodic memories."""
        return load_episodic_memories()

    def add_episode(self, summary: str, title: str, session_id: str, topics: List[str] = []) -> Dict[str, Any]:
        """
        Adds a new episodic memory.
        """
        episodes = load_episodic_memories()

        new_episode = {
            "episode_id": f"ep_{uuid.uuid4().hex[:8]}",
            "title": title,
            "summary": summary,
            "session_id": session_id,
            "key_topics": topics,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }

        episodes.append(new_episode)
        if save_episodic_memories(episodes):
            logger.info(f"Added new episode: {new_episode['episode_id']}")
            return new_episode
        else:
            logger.error("Failed to save new episode.")
            raise Exception("Failed to save new episode.")

    def update_episode(self, episode_id: str, summary: Optional[str] = None, title: Optional[str] = None, topics: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """
        Updates an existing episodic memory.
        """
        episodes = load_episodic_memories()
        episode_found = False
        updated_episode = None

        for ep in episodes:
            if ep.get("episode_id") == episode_id:
                if summary: ep["summary"] = summary
                if title: ep["title"] = title
                if topics: ep["key_topics"] = topics
                ep["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                
                episode_found = True
                updated_episode = ep
                break

        if episode_found:
            if save_episodic_memories(episodes):
                logger.info(f"Updated episode: {episode_id}")
                return updated_episode
            else:
                logger.error(f"Failed to save updated episode: {episode_id}")
                raise Exception("Failed to save updated episode.")
        else:
            logger.warning(f"Episode not found for update: {episode_id}")
            return None

    def get_all_facts(self) -> List[Dict[str, Any]]:
        """Returns all learned facts."""
        return load_learned_facts()

    def add_fact(self, text: str, category: str = "manual", source: str = "user_interface", permanence: str = "permanent") -> Dict[str, Any]:
        """
        Adds a new manually created fact.
        Args:
            text: The fact content.
            category: Classification (e.g. 'manual', 'learned_fact').
            source: Where it came from.
            permanence: 'permanent' (long-term truth) or 'transient' (temporary state, bug, current task).
        """
        facts = load_learned_facts()

        new_fact = {
            "fact_id": f"fact_{uuid.uuid4().hex[:8]}",
            "text": text,
            "category": category,
            "source": source,
            "permanence": permanence, # Store solvency Tag
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }

        facts.append(new_fact)
        if save_learned_facts(facts):
            logger.info(f"Added new fact: {new_fact['fact_id']} ({permanence})")

            # Fire-and-forget async RAG ingestion if loop is running
            if self.rag_system:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self.rag_system.ingest_fact(new_fact['text'], metadata=new_fact))
                except RuntimeError:
                    # No running loop, run synchronously with a temporary loop
                    # This ensures facts are ingested even when called from sync contexts
                    try:
                        asyncio.run(self.rag_system.ingest_fact(new_fact['text'], metadata=new_fact))
                    except Exception as e:
                        logger.warning(f"Could not ingest fact '{new_fact['fact_id']}' into RAG (sync fallback failed): {e}")

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

    def prune_transient_memories(self, age_hours: int = 24) -> int:
        """
        Removes facts marked as 'transient' that are older than age_hours.
        Returns the number of facts removed.
        """
        facts = load_learned_facts()
        if not facts: return 0
        
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=age_hours)
        keep_facts = []
        removed_count = 0
        
        for fact in facts:
            # Check permanence
            permanence = fact.get("permanence", "permanent") # Default to permanent if missing
            
            # If transient, check age
            if permanence == "transient":
                try:
                    # ISO Format parsing
                    created_at_str = fact.get("created_at")
                    # Handle potential parsing issues if legacy formats existed
                    created_at = datetime.datetime.fromisoformat(created_at_str)
                    
                    if created_at < cutoff:
                        removed_count += 1
                        continue # Skip appending, effectively deleting
                except Exception:
                    # If date parse fails, keep it or delete? Keep to be safe.
                    pass
            
            keep_facts.append(fact)
            
        if removed_count > 0:
            if save_learned_facts(keep_facts):
                logger.info(f"Pruned {removed_count} transient memories older than {age_hours} hours.")
            else:
                logger.error("Failed to save memories after pruning.")
        
        return removed_count

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
                        # No running loop, run synchronously with a temporary loop
                        try:
                            asyncio.run(self.ingest_insight_to_rag(updated_insight))
                        except Exception as e:
                            logger.warning(f"So could not ingest insight '{insight_id}' into RAG (sync fallback failed): {e}")

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
