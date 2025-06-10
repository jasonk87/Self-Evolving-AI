# ai_assistant/core/suggestion_processor.py
import asyncio
import uuid
import json
from typing import List, Dict, Any, Optional, Tuple

# Assuming these modules are in ai_assistant.core or ai_assistant.custom_tools
try:
    from .suggestion_manager import list_suggestions, mark_suggestion_implemented
    from ..execution.action_executor import ActionExecutor
    from ..code_services.service import CodeService
    from ..custom_tools.meta_programming_tools import find_agent_tool_source # Used if LLM fails or for verification
    from ..llm_interface.ollama_client import invoke_ollama_model_async # For direct LLM call
    from ..tools.tool_system import tool_system_instance # To get list of available tools
    from ..config import get_model_for_task # To get appropriate model
except ImportError as e: # pragma: no cover
    print(f"Error importing modules in suggestion_processor.py: {e}. Fallbacks or direct paths might be needed if run standalone.")
    # Fallback for direct execution or if structure is different than expected during execution
    # This is complex to get right for all scenarios, primary execution should be via the main agent entry point
    # For now, we'll assume the primary import paths work when the agent is run as a whole.
    # If this file were to be run standalone for testing, sys.path manipulation would be needed here.
    raise


LLM_TARGET_IDENTIFICATION_PROMPT_TEMPLATE = """
You are an AI assistant helping to understand user suggestions.
Given the following list of available agent tools and a suggestion for a tool improvement, identify the specific tool (module path and function name) that the suggestion refers to.
Available tools (format: "module_path.function_name": "description", ...):
{available_tools_json_str}

Suggestion description: "{suggestion_description}"

Based on the suggestion, which tool is most likely the target?
Respond with a single JSON object containing:
- "module_path": string (e.g., "ai_assistant.custom_tools.my_tool") or null
- "function_name": string (e.g., "my_tool_function") or null
- "confidence": string ("high", "medium", "low", "none")
- "reasoning": string (brief explanation for your decision, especially if no specific tool is identified or confidence is low)

If no specific tool can be confidently identified (e.g., confidence is "low" or "none"), return null for module_path and function_name.
Only return a module_path and function_name if confidence is "high" or "medium".

JSON Response:
"""

class SuggestionProcessor:
    def __init__(self, action_executor: ActionExecutor, code_service: CodeService):
        """
        Initializes the SuggestionProcessor.
        Args:
            action_executor: An instance of ActionExecutor to dispatch actions.
            code_service: An instance of CodeService to access LLM capabilities.
        """
        self.action_executor = action_executor
        self.code_service = code_service
        # self.llm_provider = code_service.llm_provider # Direct access if needed

    async def _identify_target_tool_from_suggestion(self, suggestion_description: str) -> Optional[Dict[str, str]]:
        """
        Identifies the target tool for a suggestion using LLM.
        Returns a dictionary with "module_path", "function_name", and "reasoning" if successful.
        """
        available_tools_for_prompt = {}
        try:
            # tool_system_instance should be available if this module is imported correctly
            raw_tools = tool_system_instance.list_tools_with_sources()
            for tool_key, tool_data_list in raw_tools.items():
                if not tool_data_list: continue
                tool_data = tool_data_list[0] # Use the first source for simplicity
                full_name = f"{tool_data.get('module_path', 'unknown_module')}.{tool_data.get('function_name', tool_key)}"
                available_tools_for_prompt[full_name] = tool_data.get('description', 'No description available.')
        except Exception as e: # pragma: no cover
            print(f"SuggestionProcessor: Error listing tools from ToolSystem: {e}. Target identification may be impaired.")
            # Proceeding with an empty tool list if ToolSystem fails, LLM will have less context.

        if not available_tools_for_prompt:
            print("SuggestionProcessor: No available tools found via ToolSystem to match against suggestion. Cannot identify target tool via LLM.")
            return None

        prompt = LLM_TARGET_IDENTIFICATION_PROMPT_TEMPLATE.format(
            available_tools_json_str=json.dumps(available_tools_for_prompt, indent=2),
            suggestion_description=suggestion_description
        )

        try:
            if not self.code_service.llm_provider: # Should not happen if CodeService is initialized properly
                print("SuggestionProcessor: LLM provider not available in CodeService.") # pragma: no cover
                return None

            model_name = get_model_for_task("planning") # Use planning model or a general reasoning model

            response_str = await self.code_service.llm_provider.invoke_ollama_model_async(
                prompt,
                model_name=model_name,
                temperature=0.1 # Low temperature for more factual/deterministic identification
            )

            if response_str:
                # Basic cleaning, assuming response is primarily JSON
                cleaned_response_str = response_str.strip()
                if cleaned_response_str.startswith("```json"):
                    cleaned_response_str = cleaned_response_str[len("```json"):].strip()
                    if cleaned_response_str.endswith("```"):
                        cleaned_response_str = cleaned_response_str[:-len("```")].strip()

                parsed_response = json.loads(cleaned_response_str)
                confidence = parsed_response.get("confidence", "none").lower()

                if parsed_response.get("module_path") and parsed_response.get("function_name") and confidence in ["high", "medium"]:
                    return {
                        "module_path": parsed_response["module_path"],
                        "function_name": parsed_response["function_name"],
                        "reasoning": parsed_response.get("reasoning", "")
                    }
                else:
                    print(f"SuggestionProcessor: LLM could not confidently identify target tool for suggestion '{suggestion_description[:50]}...'. Confidence: {confidence}. Reasoning: {parsed_response.get('reasoning')}")
                    return None # Explicitly return None if not confident or missing parts
            else: # pragma: no cover
                print(f"SuggestionProcessor: LLM returned empty response for target tool ID of suggestion '{suggestion_description[:50]}...'")
                return None
        except json.JSONDecodeError as e: # pragma: no cover
            print(f"SuggestionProcessor: Error decoding JSON from LLM for target tool ID: {e}. Response: {response_str[:200]}")
            return None
        except Exception as e: # pragma: no cover
            print(f"SuggestionProcessor: Error during LLM call for target tool ID: {e}")
            return None

    async def process_pending_suggestions(self, limit: int = 1): # Default limit to 1 for now
        """
        Processes pending tool improvement suggestions.
        Identifies target tools and dispatches actions to ActionExecutor.
        """
        pending_suggestions = [
            s for s in list_suggestions() if s.get("status") == "pending" and s.get("type") == "tool_improvement"
        ]
        if not pending_suggestions:
            print("SuggestionProcessor: No pending tool improvement suggestions to process.")
            return

        print(f"SuggestionProcessor: Found {len(pending_suggestions)} pending tool improvement suggestions. Processing up to {limit}.")

        processed_count = 0
        for suggestion in pending_suggestions:
            if processed_count >= limit:
                break

            suggestion_id = suggestion['suggestion_id']
            suggestion_desc = suggestion['description']
            print(f"SuggestionProcessor: Processing suggestion ID {suggestion_id}: {suggestion_desc[:70]}...")

            target_info = await self._identify_target_tool_from_suggestion(suggestion_desc)

            if target_info and target_info.get("module_path") and target_info.get("function_name"):
                print(f"SuggestionProcessor: Identified target for suggestion {suggestion_id} as {target_info['module_path']}.{target_info['function_name']}. Preparing action. Reasoning: {target_info.get('reasoning')}")

                action_details = {
                    "module_path": target_info["module_path"],
                    "function_name": target_info["function_name"],
                    "tool_name": target_info["function_name"], # Default to function_name for logging/display
                    "suggested_code_change": None, # To be generated by CodeService via ActionExecutor
                    "suggested_change_description": suggestion_desc, # This is the instruction for CodeService
                    "original_reflection_entry_id": suggestion.get("source_reflection_id"),
                }

                proposed_action_for_ae = {
                    "action_type": "PROPOSE_TOOL_MODIFICATION",
                    "details": action_details,
                    "source_insight_id": suggestion_id # Link ActionExecutor action back to this suggestion
                }

                try:
                    success = await self.action_executor.execute_action(proposed_action_for_ae)
                    if success:
                        print(f"SuggestionProcessor: Action for suggestion {suggestion_id} successfully executed and passed tests/review. Suggestion marked implemented by ActionExecutor.")
                        # ActionExecutor is now responsible for calling mark_suggestion_implemented upon final success.
                    else:
                        print(f"SuggestionProcessor: Action for suggestion {suggestion_id} failed, was rejected by review, or failed post-modification tests.")
                        # Future: update suggestion status to "auto_attempt_failed" or similar.
                        # For now, ActionExecutor's reflection log captures this.
                except Exception as e: # pragma: no cover
                    print(f"SuggestionProcessor: Error dispatching action for suggestion {suggestion_id}: {e}")
                    # Future: mark suggestion as "processing_error"
            else:
                print(f"SuggestionProcessor: Could not identify target tool for suggestion {suggestion_id}. Skipping for now.")
                # Future: mark suggestion as "needs_manual_clarification" or similar.

            processed_count += 1
        print(f"SuggestionProcessor: Finished processing batch of {processed_count} suggestions.")

# Example of how this might be run (e.g., by a background service or scheduled task)
if __name__ == '__main__': # pragma: no cover
    from ai_assistant.core.learning import LearningAgent # For example instantiation
    from ai_assistant.llm_interface.ollama_client import OllamaProvider # For example instantiation

    async def main_suggestion_processor_test():
        # This is a very basic test and requires a running Ollama instance
        # and properly configured models.
        print("--- SuggestionProcessor __main__ Test ---")

        # Setup mock LLM provider for CodeService if needed, or ensure one is running
        # For this test, we assume CodeService can be initialized and will use its default LLM provider.
        # If OllamaProvider needs specific setup (e.g., model name), it should be done here.

        # Simplified setup for ActionExecutor and CodeService
        # In a real app, these would be part of the main agent setup.
        try:
            learning_agent_instance = LearningAgent() # Requires insights file path, provide dummy if needed
        except Exception as e_la:
            print(f"Could not init LearningAgent for test: {e_la}, using None.")
            learning_agent_instance = None

        # Ensure ActionExecutor can be initialized (it depends on LearningAgent)
        if learning_agent_instance:
            try:
                # Default CodeService initialization (relies on config for LLM provider)
                code_service_instance = CodeService()
                action_executor_instance = ActionExecutor(learning_agent=learning_agent_instance)

                processor = SuggestionProcessor(action_executor_instance, code_service_instance)

                # Add a dummy suggestion to process if the file is empty or doesn't exist
                # This ensures _identify_target_tool_from_suggestion has something to work with
                # Note: _load_suggestions in suggestion_manager already creates dummy suggestions
                # if the file is not found, so this might not be strictly necessary unless
                # we want a very specific suggestion for this test.

                print("Attempting to process pending suggestions (limit 1)...")
                await processor.process_pending_suggestions(limit=1)

                print("\nSuggestion processing test completed.")
                print("Check logs and suggestions.json for outcomes.")
                print("Note: This test requires a running Ollama instance and configured models.")
                print("It also relies on ToolSystem being populated and accessible.")

            except Exception as e_main_test:
                print(f"Error during SuggestionProcessor __main__ test setup or execution: {e_main_test}")
                print("Ensure Ollama is running and models are configured (e.g., via config.ini or environment variables).")
        else:
            print("Skipping SuggestionProcessor test execution as LearningAgent could not be initialized.")

    if __name__ == '__main__':
        asyncio.run(main_suggestion_processor_test())
```
