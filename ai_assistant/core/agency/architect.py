import json
import os
import logging
import asyncio
from typing import List, Dict
import time

from ai_assistant.core.safety.judge import judge
from ai_assistant.goals.goal_management import create_goal, save_current_goals
from ai_assistant.llm_interface.gemini_client import invoke_gemini_model_async
from ai_assistant.memory.episodic_manager import EpisodicMemoryManager
from ai_assistant.config import DEFAULT_MODEL

logger = logging.getLogger(__name__)

class SystemArchitect:
    def __init__(self):
        self.event_log_path = os.path.join("ai_assistant", "core", "data", "event_log.json")
        self.episodic_memory = EpisodicMemoryManager()

    async def scan_logs(self) -> List[str]:
        """
        Scans event_log.json and episodic memory for issues.
        Returns a list of issue descriptions.
        """
        issues = []

        # 1. Scan event logs
        if os.path.exists(self.event_log_path):
            try:
                with open(self.event_log_path, 'r', encoding='utf-8') as f:
                    # Handle potential JSON decode errors or empty file
                    content = f.read()
                    if content:
                        logs = json.loads(content)
                        if isinstance(logs, list):
                            # Simple heuristic: Look for ERROR or FAILURE in logs
                            # In a real system, this would be more sophisticated (e.g., frequency analysis)
                            error_logs = [log for log in logs if isinstance(log, dict) and log.get("level") == "ERROR"]

                            # Group similar errors (naive grouping by message start)
                            error_counts = {}
                            for log in error_logs:
                                msg = log.get("message", "Unknown error")
                                key = msg[:50] # Use first 50 chars as key
                                if key not in error_counts:
                                    error_counts[key] = {"count": 0, "full_msg": msg}
                                error_counts[key]["count"] += 1

                            for key, data in error_counts.items():
                                if data["count"] >= 1: # Threshold for considering it an issue
                                    issues.append(f"Frequent Error ({data['count']} occurrences): {data['full_msg']}")
            except Exception as e:
                logger.error(f"Error reading event logs: {e}")

        # 2. Scan episodic memory for recurring failures
        # We can ask the episodic memory manager for recent failures or summary of failures
        # Since recall_failures requires a prompt, we might query generic "system failure" or "tool error"
        # However, the user request says "Method: scan_logs() ... identifies recurring errors".
        # Let's try to infer from episodic memory via a broad query if possible,
        # or we can assume the user means checking the logs of *recent* episodes.
        # The EpisodicMemoryManager interface is high-level (recall_failures).
        # Let's try to query it.

        try:
             # We assume vector_store is accessible or we use a generic prompt to find failures
             # The recall_failures method searches for similar prompts.
             # Maybe we can search for "error" or "exception".
             failure_warning = await self.episodic_memory.recall_failures("system error exception crash", k=5)
             if failure_warning:
                 issues.append(f"Recurring Failures in Episodic Memory: {failure_warning}")
        except Exception as e:
            logger.error(f"Error scanning episodic memory: {e}")

        return issues

    async def propose_goals(self, issues: List[str]) -> List[Dict]:
        """
        Uses LLM to convert Issues into SMART Goals.
        """
        if not issues:
            return []

        prompt = f"""
You are the System Architect. Your job is to analyze system issues and propose SMART maintenance goals.

ISSUES DETECTED:
{json.dumps(issues, indent=2)}

TASK:
For each issue, propose a specific maintenance goal.
Each goal must be:
- Specific (What exactly to fix/improve)
- Measurable (How to verify)
- Achievable (Within the AI's capabilities)
- Relevant (Fixes the issue)
- Time-bound (Implicitly "soon")

OUTPUT FORMAT:
Return a JSON list of objects. Each object must have:
- "title": Short title (e.g., "Fix PDF Tool")
- "description": Detailed description including the SMART criteria.
- "priority": "HIGH", "MEDIUM", or "LOW"

Example:
[
  {{
    "title": "Add Error Handling to PDF Reader",
    "description": "Wrap the read logic in try/catch to prevent crashes on malformed files. Verify with unit test.",
    "priority": "HIGH"
  }}
]
"""
        try:
            response = await invoke_gemini_model_async(
                prompt=prompt, 
                model_name=DEFAULT_MODEL,
                task_name="architectural_planning"
            )
            # Parse JSON from response (handling potential markdown code blocks)
            cleaned_response = response.replace("```json", "").replace("```", "").strip()
            goals_data = json.loads(cleaned_response)
            if isinstance(goals_data, list):
                return goals_data
            else:
                logger.warning("Architect LLM returned invalid JSON structure (not a list).")
                return []
        except Exception as e:
            logger.error(f"Error proposing goals: {e}")
            return []

    async def vet_goals(self, goals: List[Dict]) -> List[Dict]:
        """
        Passes each proposed goal to ConstitutionalJudge.evaluate_action.
        Discards any BLOCKED goals.
        """
        approved_goals = []
        loop = asyncio.get_running_loop()

        for goal_data in goals:
            title = goal_data.get("title", "Untitled Goal")
            description = goal_data.get("description", "")

            # The judge expects an action description.
            action_desc = f"Create Maintenance Goal: {title}. Description: {description}"

            # Run blocking judge in executor
            verdict = await loop.run_in_executor(
                None,
                judge.evaluate_action,
                action_desc
            )

            if verdict.status == "APPROVED":
                approved_goals.append(goal_data)
            else:
                logger.info(f"Goal blocked by Judge: {title}. Reason: {verdict.reason}")

        return approved_goals

    async def run_cycle(self) -> str:
        """
        Runs the full Architect cycle: Scan -> Propose -> Vet -> Save.
        """
        logger.info("Starting Architect cycle...")
        issues = await self.scan_logs()
        if not issues:
            return "No significant issues found in logs."

        proposed_goals = await self.propose_goals(issues)
        if not proposed_goals:
            return f"Identified {len(issues)} issues but could not formulate goals."

        approved_goals_data = await self.vet_goals(proposed_goals)

        new_goals_count = 0
        for goal_data in approved_goals_data:
            create_goal(
                title=goal_data.get("title"),
                description=goal_data.get("description"),
                priority=goal_data.get("priority", "MEDIUM"),
                status="PENDING_APPROVAL",
                metadata={
                    "type": "architect_source_change",
                    "requires_user_approval": True,
                    "created_at": time.time(),
                },
            )
            new_goals_count += 1

        if new_goals_count > 0:
            if save_current_goals():
                return f"I have identified {new_goals_count} new goals for system improvement. View them in the dashboard."
            else:
                return "Error saving goals."
        else:
            return "No goals were approved by the Constitutional Judge."
