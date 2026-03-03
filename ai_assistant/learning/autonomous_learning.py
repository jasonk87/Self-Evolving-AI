# ai_assistant/learning/autonomous_learning.py

import json
from typing import List, Optional

from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async
from ai_assistant.config import get_model_for_task, is_debug_mode
import re # For cleaning LLM response

# Import the LLM-powered curation function from knowledge_tools
from ai_assistant.custom_tools.knowledge_tools import _curate_and_update_fact_store

FACT_EXTRACTION_PROMPT_TEMPLATE = """
You are an AI assistant analyzing a conversation snippet to identify potential facts that can be learned and stored.
Focus on factual statements made by the user or the AI that are general knowledge or specific to the user's context if they seem like persistent information (e.g., user's name, preferences).
Avoid extracting questions, commands, opinions, or highly transient information.

Conversation Snippet:
---
{conversation_snippet}
---

Based on the snippet, identify distinct factual statements.
If no clear facts can be extracted, respond with the exact string "NO_FACTS_IDENTIFIED".
Otherwise, respond with a JSON object containing a single key "facts", which is a list of strings. Each string should be a self-contained factual statement. Strive to capture user-specific information in a canonical way.

Example 1:
Conversation Snippet:
---
User: My favorite color is blue.
AI: That's a nice color! I also learned that the capital of France is Paris.
---
Expected JSON Response:
{{
  "facts": [
    "The user's favorite color is blue.",
    "The capital of France is Paris."
  ]
}}

Example 2:
Conversation Snippet:
---
User: Can you search for nearby coffee shops?
AI: Sure, I found three nearby.
---
Expected JSON Response:
NO_FACTS_IDENTIFIED

Example 3:
Conversation Snippet:
---
User: My cat's name is Whiskers.
AI: Whiskers is a lovely name for a cat.
---
Expected JSON Response:
{{
  "facts": [
    "The user's cat's name is Whiskers."
  ]
}}

Example 4 (User Name Identification):
Conversation Snippet:
---
User: Hi, my name is Alex.
AI: Hello Alex! Nice to meet you.
---
Expected JSON Response:
{{
  "facts": [
    "The user's name is Alex."
  ]
}}

Example 5 (User Name Identification - different phrasing):
Conversation Snippet:
---
User: You can call me Dr. Smith.
AI: Understood, Dr. Smith.
---
Expected JSON Response:
{{
  "facts": [
    "The user's name is Dr. Smith."
  ]
}}

Respond only with the JSON object or the "NO_FACTS_IDENTIFIED" string.
"""

async def extract_potential_facts(conversation_snippet: str, llm_model_name: Optional[str] = None) -> List[str]:
    """
    Analyzes a conversation snippet using an LLM to extract potential factual statements.

    Args:
        conversation_snippet: A string representing the part of the conversation to analyze.
        llm_model_name: Optional name of the LLM model to use. If None, uses the default
                        model configured for "fact_extraction".

    Returns:
        A list of strings, where each string is an extracted factual statement.
        Returns an empty list if no facts are identified or if an error occurs.
    """
    model_to_use = llm_model_name if llm_model_name is not None else get_model_for_task("fact_extraction")
    
    prompt = FACT_EXTRACTION_PROMPT_TEMPLATE.format(conversation_snippet=conversation_snippet)

    if is_debug_mode():
        print(f"[DEBUG AUTONOMOUS_LEARNING] Extracting facts from conversation snippet (first 100 chars): {conversation_snippet[:100]}...")
        # print(f"[DEBUG AUTONOMOUS_LEARNING] Fact extraction prompt (first 300 chars):\n{prompt[:300]}...") 

    llm_response = await invoke_ollama_model_async(prompt, model_name=model_to_use)

    if not llm_response or not llm_response.strip():
        print("Warning (extract_potential_facts): Received no or empty response from LLM for fact extraction.")
        return []

    llm_response = llm_response.strip()
    if is_debug_mode():
        print(f"[DEBUG AUTONOMOUS_LEARNING] Raw LLM response for fact extraction:\n'{llm_response}'")

    if llm_response == "NO_FACTS_IDENTIFIED":
        return []

    cleaned_response = llm_response
    if cleaned_response.startswith("```json"):
        cleaned_response = cleaned_response.lstrip("```json").rstrip("```").strip()
    elif cleaned_response.startswith("```"):
        cleaned_response = cleaned_response.lstrip("```").rstrip("```").strip()

    try:
        parsed_response = json.loads(cleaned_response)
        
        if not isinstance(parsed_response, dict):
            print(f"Warning (extract_potential_facts): LLM response for fact extraction was not a JSON dictionary. Response: {cleaned_response}")
            return []
            
        facts = parsed_response.get("facts")
        
        if facts is None:
            print(f"Warning (extract_potential_facts): LLM response JSON is missing 'facts' key. Response: {cleaned_response}")
            return []

        if not isinstance(facts, list) or not all(isinstance(fact, str) for fact in facts):
            print(f"Warning (extract_potential_facts): LLM response 'facts' key is not a list of strings. Response: {parsed_response}")
            return []
            
        return facts

    except json.JSONDecodeError:
        print(f"Error (extract_potential_facts): Failed to parse LLM response as JSON for fact extraction. Response: {cleaned_response}")
        return []
    except Exception as e:
        print(f"Error (extract_potential_facts): An unexpected error occurred during LLM response processing for fact extraction: {e}")
        return []


async def learn_facts_from_interaction(user_input: str, ai_response: str, enabled: bool) -> Optional[List[str]]:
    """
    Orchestrates the process of extracting potential facts from a user-AI interaction
    and then submitting them to the knowledge base for LLM-powered curation and storage.

    Args:
        user_input: The input provided by the user.
        ai_response: The response generated by the AI.
        enabled: A boolean flag to enable or disable this learning feature.

    Returns:
        A list of fact strings that were *sent* for curation if the process was initiated 
        and the curation step reported success.
        Returns None if the feature is disabled, no facts were extracted, 
        or the curation process failed.
    """
    if not enabled:
        return None

    if not user_input and not ai_response:
        return None # Nothing to process

    conversation_snippet = f"User: {user_input}\nAI: {ai_response}"
    
    if is_debug_mode():
        print(f"[DEBUG AUTONOMOUS_LEARNING] Analyzing snippet for autonomous learning:\n{conversation_snippet}")

    extracted_facts = await extract_potential_facts(conversation_snippet)

    if not extracted_facts:
        if is_debug_mode():
            print("[DEBUG AUTONOMOUS_LEARNING] No potential facts extracted for autonomous learning.")
        return None

    # Filter out empty or whitespace-only strings from extracted_facts before sending to curator
    valid_extracted_facts = [fact for fact in extracted_facts if isinstance(fact, str) and fact.strip()]

    if not valid_extracted_facts:
        if is_debug_mode():
            print("[DEBUG AUTONOMOUS_LEARNING] All extracted facts were empty or invalid. No facts sent for curation.")
        return None
    
    if is_debug_mode():
        print(f"[DEBUG AUTONOMOUS_LEARNING] Sending {len(valid_extracted_facts)} extracted fact(s) for LLM curation: {valid_extracted_facts}")

    # Call the curation function from knowledge_tools with the batch of extracted facts
    # _curate_and_update_fact_store is already async
    curation_successful = await _curate_and_update_fact_store(valid_extracted_facts)
    
    if curation_successful:
        if is_debug_mode():
            print(f"[DEBUG AUTONOMOUS_LEARNING] Fact curation process reported success for: {valid_extracted_facts}")
        # Return the facts that were sent for curation, as an indication of what was processed.
        # The actual learned facts might be different due to LLM's curation.
        return valid_extracted_facts 
    else:
        if is_debug_mode():
            print(f"[DEBUG AUTONOMOUS_LEARNING] Fact curation process failed for: {valid_extracted_facts}")
        return None

if __name__ == '__main__': # pragma: no cover
    import asyncio
    import os
    from ai_assistant.memory.persistent_memory import save_learned_facts, load_learned_facts, LEARNED_FACTS_FILEPATH
    # We need to import the real _curate_and_update_fact_store from knowledge_tools for the __main__ block's patching logic
    # or ensure the global patch targets the correct module.
    # The import `from ai_assistant.custom_tools.knowledge_tools import _curate_and_update_fact_store` at the top
    # is what learn_facts_from_interaction will use.

    # --- Test Setup ---
    if not os.path.exists("data"):
        os.makedirs("data", exist_ok=True)
    
    DEFAULT_FACTS_FILE = LEARNED_FACTS_FILEPATH 
    original_facts_content_main = None
    if os.path.exists(DEFAULT_FACTS_FILE):
        with open(DEFAULT_FACTS_FILE, 'r', encoding='utf-8') as f_orig_main:
            original_facts_content_main = f_orig_main.read()
    save_learned_facts([]) 

    # Mock invoke_ollama_model_async for extract_potential_facts
    async def mock_fact_extraction_llm_main(prompt: str, model_name: str, **kwargs):
        if "My favorite color is blue" in prompt and "capital of France is Paris" in prompt:
            return json.dumps({"facts": ["The user's favorite color is blue.", "The capital of France is Paris."]})
        elif "My cat's name is Whiskers" in prompt:
            return json.dumps({"facts": ["The user's cat's name is Whiskers."]})
        elif "my name is Alex" in prompt.lower(): # Test for new name extraction example
            return json.dumps({"facts": ["The user's name is Alex."]})
        elif "call me dr. smith" in prompt.lower(): # Test for another name extraction example
            return json.dumps({"facts": ["The user's name is Dr. Smith."]})
        elif "search for nearby coffee shops" in prompt:
            return "NO_FACTS_IDENTIFIED"
        return json.dumps({"facts": []}) 

    # Mock _curate_and_update_fact_store from knowledge_tools for testing autonomous_learning.py
    _mock_main_curated_facts_store: List[str] = [] 
    async def mock_curate_store_main(newly_observed: List[str]):
        global _mock_main_curated_facts_store
        
        current_facts_for_mock_curation = list(_mock_main_curated_facts_store)
        
        updated_for_mock_curation = list(current_facts_for_mock_curation)
        for f_new in newly_observed:
            # Simple de-duplication for mock; real one is LLM based
            is_new_name_fact = "The user's name is " in f_new
            existing_name_fact_index = -1
            if is_new_name_fact:
                for i, existing_f in enumerate(updated_for_mock_curation):
                    if "The user's name is " in existing_f:
                        existing_name_fact_index = i
                        break
            
            if existing_name_fact_index != -1: # Update existing name fact
                updated_for_mock_curation[existing_name_fact_index] = f_new
            elif f_new not in updated_for_mock_curation: # Add if not a duplicate (and not updating name)
                updated_for_mock_curation.append(f_new)
        
        _mock_main_curated_facts_store = updated_for_mock_curation
        
        save_success = save_learned_facts(_mock_main_curated_facts_store, DEFAULT_FACTS_FILE)
        return save_success


    import ai_assistant.learning.autonomous_learning as this_module
    original_invoke_async_auto_learn_main = this_module.invoke_ollama_model_async
    this_module.invoke_ollama_model_async = mock_fact_extraction_llm_main

    original_curate_store_import_main = this_module._curate_and_update_fact_store 
    this_module._curate_and_update_fact_store = mock_curate_store_main


    async def run_autonomous_learning_tests_main():
        print("\n--- Testing learn_facts_from_interaction (with updated mocks for batch curation) ---")
        
        global _mock_main_curated_facts_store
        
        # Test 1: Learn new facts from interaction
        print("\nTest 1: Learn new facts from interaction")
        _mock_main_curated_facts_store = [] 
        save_learned_facts([]) 

        user_input1 = "My favorite color is blue."
        ai_response1 = "That's a nice color! I also learned that the capital of France is Paris."
        
        processed_facts1 = await learn_facts_from_interaction(user_input1, ai_response1, enabled=True)
        print(f"Processed facts from interaction 1: {processed_facts1}")
        assert processed_facts1 is not None
        assert "The user's favorite color is blue." in processed_facts1
        assert "The capital of France is Paris." in processed_facts1
        
        recalled_after_test1 = load_learned_facts(DEFAULT_FACTS_FILE) 
        print(f"Facts in store after Test 1 ({DEFAULT_FACTS_FILE}): {recalled_after_test1}")
        assert "The user's favorite color is blue." in recalled_after_test1
        assert "The capital of France is Paris." in recalled_after_test1

        # Test for name extraction
        print("\nTest Name Extraction 1: 'my name is Alex'")
        _mock_main_curated_facts_store = [] # Reset for this specific test segment
        save_learned_facts([])
        user_input_name1 = "Hi, my name is Alex."
        ai_response_name1 = "Hello Alex!"
        processed_name1 = await learn_facts_from_interaction(user_input_name1, ai_response_name1, enabled=True)
        print(f"Processed name fact 1: {processed_name1}")
        assert processed_name1 and "The user's name is Alex." in processed_name1
        recalled_name1 = load_learned_facts(DEFAULT_FACTS_FILE)
        print(f"Facts in store after name fact 1: {recalled_name1}")
        assert "The user's name is Alex." in recalled_name1

        print("\nTest Name Extraction 2: 'call me Dr. Smith' (should update previous name)")
        # Current store: ["The user's name is Alex."]
        user_input_name2 = "Actually, you can call me Dr. Smith."
        ai_response_name2 = "Understood, Dr. Smith."
        processed_name2 = await learn_facts_from_interaction(user_input_name2, ai_response_name2, enabled=True)
        print(f"Processed name fact 2: {processed_name2}")
        assert processed_name2 and "The user's name is Dr. Smith." in processed_name2
        recalled_name2 = load_learned_facts(DEFAULT_FACTS_FILE)
        print(f"Facts in store after name fact 2: {recalled_name2}")
        assert "The user's name is Dr. Smith." in recalled_name2
        assert "The user's name is Alex." not in recalled_name2 # Check if old name was replaced by mock logic
        assert len(recalled_name2) == 1 # Should only be one name fact if mock logic correctly updated

        # Restore original functions
        this_module.invoke_ollama_model_async = original_invoke_async_auto_learn_main
        this_module._curate_and_update_fact_store = original_curate_store_import_main

        # Cleanup
        print("\n--- Test Cleanup ---")
        if original_facts_content_main is not None:
            with open(DEFAULT_FACTS_FILE, 'w', encoding='utf-8') as f_restore_main:
                f_restore_main.write(original_facts_content_main)
            print(f"Restored original content to {DEFAULT_FACTS_FILE}")
        elif os.path.exists(DEFAULT_FACTS_FILE): 
            os.remove(DEFAULT_FACTS_FILE)
            print(f"Removed test file {DEFAULT_FACTS_FILE}")
        
        data_dir_main = os.path.dirname(DEFAULT_FACTS_FILE)
        if os.path.exists(data_dir_main) and not os.listdir(data_dir_main):
            try: os.rmdir(data_dir_main)
            except OSError: pass

        print("\n--- Autonomous Learning Module Tests Finished ---")

    asyncio.run(run_autonomous_learning_tests_main())
