
import logging
from typing import List, Dict, Any, Optional
import json
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async
from ai_assistant.config import get_model_for_task
from ai_assistant.learning.learning import ActionableInsight, InsightType

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT_TEMPLATE = """
You are the 'Conversational Analyst' for an AI assistant. Your goal is to review a recent chat session between a USER and the AI to identify missed learnings, persistent user frustrations, or explicit preferences.

Context: The AI is designed to start simple but "self-evolve" by creating new tools, learning facts, and improving its code.

Session Transcript:
{transcript}

Task:
Analyze the transcript above. Look for:
1. **User Frustration**: Did the user have to repeat themselves? Did they express annoyance at the AI's behavior (e.g., "stop doing X", "I already told you Y")?
2. **Explicit Directives/Preferences**: Did the user give a general instruction about how the AI should behave?
3. **Learned Facts**: Did the user state a permanent fact about themselves or the world (e.g., "My son is Thomas", "We use AWS")?
4. **Missed Failures / Tool Bugs**: Did the AI think it succeeded, but the user said it didn't?

Output:
Return a JSON object with a list of 'insights'. Each insight should have:
- 'type': One of ["USER_PREFERENCE_LEARNED", "LEARNED_FACT", "KNOWLEDGE_GAP_IDENTIFIED", "PLANNING_HEURISTIC_SUGGESTION", "USER_FRUSTRATION", "TOOL_BUG_SUSPECTED"]
- 'description': A clear, actionable description.
- 'related_tool_name': (Optional)
- 'evidence': Quote.
- 'suggestion': suggestion.

If nothing significant is found, return {{"insights": []}}.
Example JSON:
{{
  "insights": [
    {{
      "type": "LEARNED_FACT",
      "description": "User's son is named Thomas.",
      "evidence": "User said 'my sons name is Thomas'",
      "suggestion": "Save this fact to permanent memory."
    }}
  ]
}}
"""

class ConversationalAnalyst:
    def __init__(self):
        self.model = get_model_for_task("reflection") # Use a smart model for analysis

    def _format_transcript(self, session_data: Dict) -> str:
        history = session_data.get("history", [])
        transcript = []
        for msg in history:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            # Truncate very long content to avoid context window issues
            # Truncate very long content to avoid context window issues
            if len(content) > 50000:
                content = content[:49997] + "..."
            transcript.append(f"{role}: {content}")
        return "\n\n".join(transcript)

    async def analyze_session_transcript(self, session_data: Dict) -> List[ActionableInsight]:
        transcript = self._format_transcript(session_data)
        if not transcript.strip():
            return []

        prompt = ANALYSIS_PROMPT_TEMPLATE.format(transcript=transcript)
        
        try:
            response = await invoke_ollama_model_async(prompt, model_name=self.model)
            if not response:
                return []
            
            # Parse JSON
            json_str = response
            if "```json" in response:
                json_str = response.split("```json")[1].split("```")[0]
            elif "```" in response:
                json_str = response.split("```")[1].split("```")[0]
            
            data = json.loads(json_str)
            insights_data = data.get("insights", [])
            
            actionable_insights = []
            for item in insights_data:
                insight_type_str = item.get("type")
                try:
                    # Map string to Enum (Explicit mapping for safety)
                    itype = InsightType.PLANNING_HEURISTIC_SUGGESTION # Default
                    
                    if insight_type_str == "USER_PREFERENCE_LEARNED": itype = InsightType.USER_PREFERENCE_LEARNED
                    elif insight_type_str == "USER_FRUSTRATION": itype = InsightType.USER_FRUSTRATION
                    elif insight_type_str == "KNOWLEDGE_GAP_IDENTIFIED": itype = InsightType.KNOWLEDGE_GAP_IDENTIFIED
                    elif insight_type_str == "PLANNING_HEURISTIC_SUGGESTION": itype = InsightType.PLANNING_HEURISTIC_SUGGESTION
                    elif insight_type_str == "TOOL_BUG_SUSPECTED": itype = InsightType.TOOL_BUG_SUSPECTED
                    elif insight_type_str == "LEARNED_FACT": itype = InsightType.LEARNED_FACT # NEW
                    else:
                        logger.warning(f"ConversationalAnalyst: Unknown insight type '{insight_type_str}'.")
                    
                    description = f"{item.get('description')} (Evidence: {item.get('evidence')})"
                    if itype == InsightType.TOOL_BUG_SUSPECTED:
                        description = f"ISSUE DETECTED: {description}"
                    
                    suggestion = item.get("suggestion")
                    
                    # Create actionable insight
                    # We use a dummy source ID for now as we don't link to exact reflection entries yet
                    # In future, we could link to session ID.
                    insight = ActionableInsight(
                        type=itype,
                        description=description,
                        source_reflection_entry_ids=[], 
                        priority=3,
                        related_tool_name=item.get("related_tool_name"), # Extract tool name
                        metadata={
                            "source": "conversational_analysis",
                            "session_id": session_data.get("id"),
                            "suggestion_text": suggestion
                        }
                    )
                    
                    # Map suggestions to specific fields if possible
                    if itype == InsightType.KNOWLEDGE_GAP_IDENTIFIED:
                        insight.knowledge_to_learn = suggestion
                    elif itype == InsightType.PLANNING_HEURISTIC_SUGGESTION:
                        insight.planning_heuristic_details = {"suggestion": suggestion}
                    
                    actionable_insights.append(insight)
                        
                except Exception as e:
                    logger.error(f"ConversationalAnalyst: Error processing insight item: {e}")
            
            return actionable_insights

        except json.JSONDecodeError:
            logger.warning("ConversationalAnalyst: Failed to parse LLM response as JSON.")
            return []
        except Exception as e:
            logger.error(f"ConversationalAnalyst: Unexpected error: {e}")
            return []

