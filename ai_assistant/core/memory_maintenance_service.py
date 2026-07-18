import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

from ai_assistant.memory.persistent_memory import (
    load_learned_facts, save_learned_facts, load_actionable_insights, save_actionable_insights
)
from ai_assistant.core.memory_manager import MemoryManager
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async
from ai_assistant.config import DEFAULT_MODEL

logger = logging.getLogger(__name__)

AUDIT_PROMPT_TEMPLATE = """
You are a Memory Auditor. Your job is to classify the following facts as either "permanent" (useful long-term knowledge, user preferences, core system info) or "transient" (temporary errors, status updates, debugging notes that are no longer relevant).

Facts to classify:
{facts_list}

Respond with a JSON object where keys are the fact IDs and values are the classification ("permanent" or "transient").

Example:
{{
  "fact_123": "permanent",
  "fact_456": "transient"
}}
"""

class MemoryMaintenanceService:
    """
    Service responsible for periodic maintenance of the AI's memory.
    - Audits legacy facts (missing permanence tags).
    - Prunes transient memories older than a threshold.
    """

    def __init__(self, memory_manager: Optional[MemoryManager] = None):
        self.memory_manager = memory_manager or MemoryManager()
        self.batch_size = 20

    async def run_maintenance_cycle(self) -> Dict[str, int]:
        """
        Runs a full maintenance cycle:
        1. Classify legacy untagged facts.
        2. Prune outdated transient memories.
        """
        logger.info("MemoryMaintenanceService: Starting maintenance cycle...")
        stats = {
            "legacy_classified": 0,
            "transient_pruned": 0,
            "insights_pruned": 0
        }

        # 1. Prune Stale Insights (Tool Bugs, etc.)
        try:
            insight_pruned_count = self.prune_stale_insights(age_hours=24)
            stats["insights_pruned"] = insight_pruned_count
        except Exception as e:
            logger.error(f"MemoryMaintenanceService: Error during insight pruning: {e}", exc_info=True)

        # 2. Audit Legacy Facts
        try:
            classified_count = await self._audit_legacy_facts()
            stats["legacy_classified"] = classified_count
        except Exception as e:
            logger.error(f"MemoryMaintenanceService: Error during legacy audit: {e}", exc_info=True)

        # 3. Prune Transient Memories
        try:
            # Default to 24 hours retention for transient facts
            pruned_count = self.memory_manager.prune_transient_memories(age_hours=24)
            stats["transient_pruned"] = pruned_count
        except Exception as e:
            logger.error(f"MemoryMaintenanceService: Error during pruning: {e}", exc_info=True)

        logger.info(f"MemoryMaintenanceService: Cycle complete. Stats: {stats}")
        return stats

    async def _audit_legacy_facts(self) -> int:
        """
        Identifies and classifies facts that are missing the 'permanence' tag.
        Returns the number of facts updated.
        """
        facts = load_learned_facts()
        if not facts:
            return 0

        # Identify Legacy Facts
        legacy_facts = []
        for f in facts:
            if "permanence" not in f or f["permanence"] not in ["permanent", "transient"]:
                legacy_facts.append(f)

        if not legacy_facts:
            logger.debug("MemoryMaintenanceService: No legacy facts found.")
            return 0

        logger.info(f"MemoryMaintenanceService: Found {len(legacy_facts)} legacy facts to classify.")

        # Backup before modification check?
        # PersistentMemory methods usually handle atomic writes, but a backup is safe.
        # We'll rely on the source file existing.

        modifications = {}

        # Batch Process
        for i in range(0, len(legacy_facts), self.batch_size):
            batch = legacy_facts[i:i+self.batch_size]

            facts_list_str = ""
            for f in batch:
                text = f.get('text', '')[:200]
                facts_list_str += f"- ID: {f['fact_id']}\n  Text: {text}\n"

            prompt = AUDIT_PROMPT_TEMPLATE.format(facts_list=facts_list_str)

            try:
                response = await invoke_ollama_model_async(prompt, model_name=DEFAULT_MODEL)
                if not response: continue

                # Robust JSON Parse
                json_str = response
                if "```json" in response:
                    json_str = response.split("```json")[1].split("```")[0]
                elif "```" in response:
                    json_str = response.split("```")[1].split("```")[0]

                batch_results = json.loads(json_str)
                modifications.update(batch_results)

            except Exception as e:
                logger.warning(f"MemoryMaintenanceService: Error processing batch {i}: {e}")

        # Apply Updates
        updated_count = 0
        if modifications:
             # Reload facts to ensure we have latest state (though unlikely to change rapidly in background)
             # But let's work on the 'facts' list we have since persistent_memory invalidates cache on save
             for f in facts:
                fid = f["fact_id"]
                if fid in modifications:
                    tag = modifications[fid]
                    if tag in ["permanent", "transient"]:
                        f["permanence"] = tag
                        updated_count += 1

             if updated_count > 0:
                if save_learned_facts(facts):
                    logger.info(f"MemoryMaintenanceService: Successfully classified {updated_count} legacy facts.")
                else:
                    logger.error("MemoryMaintenanceService: Failed to save classified facts.")

        return updated_count

    def prune_stale_insights(self, age_hours: int = 24) -> int:
        """
        Removes 'TOOL_BUG_SUSPECTED' insights that are older than age_hours.
        Returns the number of insights removed.
        """
        insights = load_actionable_insights()
        if not insights:
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(hours=age_hours)
        keep_insights = []
        removed_count = 0

        for insight in insights:
            # Check type
            insight_type = insight.get("type")

            # Pruning Rules:
            # 1. TOOL_BUG_SUSPECTED older than cutoff
            # 2. HYPOTHETICAL_SCENARIO (Dream results) older than cutoff (often clutter)
            if insight_type in ["TOOL_BUG_SUSPECTED", "HYPOTHETICAL_SCENARIO"]:
                 try:
                    # Parse timestamp
                    ts_str = insight.get("creation_timestamp")
                    if ts_str:
                        # Handle varied formats if necessary, but Isoformat is standard
                        created_at = datetime.fromisoformat(ts_str)
                        if created_at < cutoff:
                            removed_count += 1
                            continue # Skip appending
                 except Exception:
                     # If format error, conservatively keep it
                     pass

            keep_insights.append(insight)

        if removed_count > 0:
            if save_actionable_insights(keep_insights):
                logger.info(f"MemoryMaintenanceService: Pruned {removed_count} stale insights (Tool Bugs/Dreams older than {age_hours}h).")
            else:
                logger.error("MemoryMaintenanceService: Failed to save insights after pruning.")

        return removed_count
