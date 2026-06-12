
import os
import uuid
import json
import logging
from typing import Optional
from ai_assistant.core.reflection import InsightType
from ai_assistant.memory.persistent_memory import ACTIONABLE_INSIGHTS_FILEPATH

logger = logging.getLogger(__name__)

def report_tool_issue(
    tool_name: str,
    issue_description: str,
    suggested_fix: Optional[str] = None
) -> str:
    """
    Reports a suspected bug or issue with a specific tool. This creates an actionable insight
    that will be processed by the LearningAgent to analyze the root cause and propose a fix.

    Args:
        tool_name: The name of the tool suspected to have a bug (e.g., 'add_numbers').
        issue_description: A clear description of the observed issue or error.
        suggested_fix: Optional. A suggestion for how to fix it, if known.

    Returns:
        A confirmation message indicating the report has been filed.
    """
    try:
        # Load existing insights
        existing_insights = []
        if os.path.exists(ACTIONABLE_INSIGHTS_FILEPATH):
            try:
                with open(ACTIONABLE_INSIGHTS_FILEPATH, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        data = json.loads(content)
                        if isinstance(data, list):
                            existing_insights = data
            except json.JSONDecodeError:
                logger.warning(f"Could not parse existing insights file at {ACTIONABLE_INSIGHTS_FILEPATH}. Starting fresh.")
            except Exception as e:
                logger.error(f"Error reading insights file: {e}")

        # Create new insight
        insight_id = f"insight_{uuid.uuid4()}"
        description = f"User reported issue with tool '{tool_name}': {issue_description}"
        if suggested_fix:
            description += f"\nUser suggested fix: {suggested_fix}"

        new_insight = {
            "insight_id": insight_id,
            "type": InsightType.TOOL_BUG_SUSPECTED.value if hasattr(InsightType, "value") else "TOOL_BUG_SUSPECTED",
            "description": description,
            "related_tool_name": tool_name,
            "priority": 2, # High priority for direct user reports
            "status": "NEW",
            "creation_timestamp": str(uuid.uuid1()), # Use UUID structure if datetime not imported, or just ISO string
            "source_reflection_entry_ids": [], # No direct reflection log entry for chat report
            "metadata": {
                "source": "user_chat_report",
                "suggested_fix_from_user": suggested_fix
            }
        }
        
        # Add timestamp (importing datetime here to keep scope clean if not needed elsewhere)
        import datetime
        new_insight["creation_timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

        existing_insights.append(new_insight)

        # Save back
        with open(ACTIONABLE_INSIGHTS_FILEPATH, 'w', encoding='utf-8') as f:
            json.dump(existing_insights, f, indent=2)

        return f"Issue reported for tool '{tool_name}'. The system will analyze it shortly."

    except Exception as e:
        logger.error(f"Error checking reporting tool issue: {e}", exc_info=True)
        return f"Error reporting issue: {e}"

# Conceptual Schema for report_tool_issue
REPORT_TOOL_ISSUE_SCHEMA = {
    "name": "report_tool_issue",
    "description": "Reports a bug or issue with a tool. Use this when the user claims a tool is broken or not working as expected. This triggers an internal analysis.",
    "parameters": [
        tuple(sorted({"name": "tool_name", "type": "str", "description": "The name of the tool."}.items())),
        tuple(sorted({"name": "issue_description", "type": "str", "description": "Description of the issue."}.items())),
        tuple(sorted({"name": "suggested_fix", "type": "str", "description": "Optional user suggestion."}.items()))
    ],
    "returns": tuple(sorted({
        "type": "string",
        "description": "Confirmation message."
    }.items()))
}
