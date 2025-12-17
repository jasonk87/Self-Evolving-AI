import json
import logging
from typing import List, Dict, Any, Optional, Set
# Removed direct import of invoke_ollama_model_async
from ai_assistant.config import get_model_for_task

logger = logging.getLogger(__name__)

LLM_SIMULATION_PROMPT_TEMPLATE = """
You are the 'Shadow Mode' Simulator. Your job is to predict the effect of a project plan step on the project's file system and identify potential logic errors or risks.

Current Virtual File System State:
{current_files_state}

Proposed Plan Step:
- Step ID: {step_id}
- Type: {step_type}
- Description: {step_description}
- Details: {step_details}

Your Task:
1. Predict how this step changes the file system (files created, deleted, or modified).
2. specific check for LOGIC ERRORS, such as:
   - Editing a file that doesn't exist.
   - Reading a file that was just deleted.
   - Creating a file that already exists (without overwriting intent).
   - Using a dependency that hasn't been installed/created.
3. specific check for SAFETY RISKS, such as:
   - Deleting critical system files.
   - Infinite loops.

Response Format (JSON ONLY):
{{
  "files_added": ["path/to/new_file.py", ...],
  "files_removed": ["path/to/deleted_file.py", ...],
  "files_modified": ["path/to/modified_file.py", ...],
  "risk_level": "NONE" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
  "risk_reason": "Explanation of the risk or error (or null if none)",
  "predicted_outcome": "Brief summary of what will happen."
}}

Analyze the step and return the JSON.
"""

class PlanSimulator:
    def __init__(self, initial_files: Optional[List[str]] = None, llm_provider: Any = None):
        """
        Initializes the PlanSimulator.
        Args:
            initial_files: List of file paths representing the starting state.
            llm_provider: Instance of the LLM provider (e.g. OllamaProvider).
        """
        self.initial_files_snapshot: Set[str] = set(initial_files) if initial_files else set()
        self.virtual_files: Set[str] = self.initial_files_snapshot.copy()
        self.simulation_log: List[Dict[str, Any]] = []
        self.llm_provider = llm_provider

    def reset_state(self):
        """
        Resets the virtual file system to the initial snapshot.
        Useful for retrying simulations.
        """
        self.virtual_files = self.initial_files_snapshot.copy()
        self.simulation_log = []

    async def simulate_plan(self, plan: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Runs a simulation of the entire plan.
        Returns a report containing the pass/fail status and any issues found.
        """
        issues = []
        logger.info(f"Starting Plan Simulation for {len(plan)} steps.")

        for step in plan:
            result = await self._simulate_step(step)
            self.simulation_log.append({
                "step_id": step.get("step_id"),
                "result": result
            })

            if result["risk_level"] in ["HIGH", "CRITICAL"]:
                issues.append({
                    "step_id": step.get("step_id"),
                    "risk_level": result["risk_level"],
                    "reason": result["risk_reason"]
                })

            # Update virtual file state
            self._apply_state_changes(result)

        success = len(issues) == 0
        return {
            "success": success,
            "issues": issues,
            "simulation_log": self.simulation_log
        }

    async def _simulate_step(self, step: Dict[str, Any]) -> Dict[str, Any]:
        """
        Simulates a single step using the LLM.
        """
        # Format current files for prompt (abbreviated if too long)
        files_list = sorted(list(self.virtual_files))
        if len(files_list) > 50:
            files_display = "\n".join(files_list[:50]) + f"\n... ({len(files_list)-50} more)"
        else:
            files_display = "\n".join(files_list) if files_list else "(Empty Project)"

        # Prepare details string
        details_str = json.dumps(step.get("details", {}))

        prompt = LLM_SIMULATION_PROMPT_TEMPLATE.format(
            current_files_state=files_display,
            step_id=step.get("step_id", "Unknown"),
            step_type=step.get("type", "Unknown"),
            step_description=step.get("description", ""),
            step_details=details_str
        )

        model_name = get_model_for_task("reasoning") # Use a smart model for simulation

        try:
            if self.llm_provider:
                response = await self.llm_provider.invoke_ollama_model_async(
                    prompt,
                    model_name=model_name,
                    temperature=0.1, # Low temp for deterministic logic
                    max_tokens=500
                )
            else:
                 # Fallback for tests if provider not passed (though tests should mock it)
                 logger.warning("PlanSimulator: No LLM provider configured.")
                 return {
                    "files_added": [], "files_removed": [], "files_modified": [],
                    "risk_level": "LOW", "risk_reason": "No LLM Provider", "predicted_outcome": "Skipped"
                 }

            # Parse JSON
            result = self._parse_json_response(response)
            if not result:
                # Fallback if parsing fails
                return {
                    "files_added": [],
                    "files_removed": [],
                    "files_modified": [],
                    "risk_level": "LOW",
                    "risk_reason": "Failed to parse Simulation LLM response.",
                    "predicted_outcome": "Unknown"
                }
            return result

        except Exception as e:
            logger.error(f"PlanSimulator: Error simulating step {step.get('step_id')}: {e}")
            return {
                "files_added": [],
                "files_removed": [],
                "files_modified": [],
                "risk_level": "LOW", # Default to low risk on error
                "risk_reason": f"Simulation Exception: {e}",
                "predicted_outcome": "Simulation Failed"
            }

    def _apply_state_changes(self, result: Dict[str, Any]):
        """
        Updates the virtual file system based on the simulation result.
        """
        for f in result.get("files_added", []):
            self.virtual_files.add(f)

        for f in result.get("files_removed", []):
            if f in self.virtual_files:
                self.virtual_files.remove(f)

    def _parse_json_response(self, response: str) -> Optional[Dict[str, Any]]:
        """
        Robustly parses the JSON response from the LLM.
        """
        try:
            # Strip markdown code blocks
            clean_resp = response.strip()
            if "```json" in clean_resp:
                clean_resp = clean_resp.split("```json")[1].split("```")[0].strip()
            elif "```" in clean_resp:
                clean_resp = clean_resp.split("```")[1].split("```")[0].strip()

            data = json.loads(clean_resp)

            # Normalize keys just in case
            return {
                "files_added": data.get("files_added", []),
                "files_removed": data.get("files_removed", []),
                "files_modified": data.get("files_modified", []),
                "risk_level": data.get("risk_level", "NONE"),
                "risk_reason": data.get("risk_reason"),
                "predicted_outcome": data.get("predicted_outcome", "")
            }
        except Exception:
            return None
