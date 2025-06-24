# ai_assistant/custom_tools/knowledge_tools.py
import json
from typing import Optional, List
from ai_assistant.memory.persistent_memory import load_learned_facts, save_learned_facts
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async # Changed to async
from ai_assistant.config import get_model_for_task, is_debug_mode
import re # For cleaning LLM response

FACT_CURATION_PROMPT_TEMPLATE = """
You are an AI Knowledge Base Curator. Your primary responsibility is to maintain a clean, accurate, and non-redundant set of learned facts.
You will be given the CURRENT set of learned facts and a list of NEWLY OBSERVED potential facts.

Your task is to produce an UPDATED AND FINALIZED list of facts by performing the following operations:
1.  **Integrate New Information:** If a newly observed item provides genuinely new and persistent factual information, add it.
2.  **Consolidate & Refine:** If a new item is related to an existing fact but adds more detail, clarifies, or slightly corrects it, merge them into a single, more comprehensive fact. Prefer the more accurate or detailed version.
3.  **Eliminate Duplicates:** If a new item is identical or semantically equivalent to an existing fact, do not add the new item. Ensure the existing fact is retained if it's well-phrased.
4.  **Correct/Update:** If a new item clearly contradicts and updates an older fact (e.g., "User's favorite color was blue" and new is "User's favorite color is now green"), replace the older fact.
5.  **Discard Non-Factual/Transient Info:** If a newly observed item is not a persistent fact (e.g., it's a command, a question, a temporary state, an opinion, or a very specific detail unlikely to be broadly useful later), discard it.
    *   Examples of info to DISCARD: "run the search tool", "what is the weather?", "I feel tired today", "the file is missing right now", "tell me about X", "create a tool for Y".
    *   Examples of info to KEEP (if relevant and persistent): "The user's name is Alex.", "The project name is 'hangman'", "The project description is 'Create a hangman game...'", "The user prefers Python.", "The capital of France is Paris."
6.  **Maintain Conciseness & Clarity:** Ensure facts are stated clearly and concisely. Avoid overly long or ambiguous statements. Rephrase if necessary for clarity.
    *   Example of Refinement: Change "User mentioned their name is Alex Smith." to "The user's name is Alex Smith."

CURRENT LEARNED FACTS (JSON list of strings):
{current_facts_json}

NEWLY OBSERVED POTENTIAL FACTS (JSON list of strings):
{new_potential_facts_json}

Based on these inputs, output the UPDATED AND FINALIZED list of facts.
The output *MUST* be a JSON object containing a single key "updated_facts", which is a list of strings.

Example:
CURRENT LEARNED FACTS:
["The user's name is Jason.", "The capital of France is Paris."]
NEWLY OBSERVED POTENTIAL FACTS:
["User mentioned their name is Jason.", "Paris is in France."]

Expected JSON Response:
{{
  "updated_facts": [
    "The user's name is Jason",
    "The capital of France is Paris."
  ]
}}

If NEWLY OBSERVED POTENTIAL FACTS is empty, or if after processing no changes are warranted to CURRENT LEARNED FACTS, then the "updated_facts" list should be identical to the CURRENT LEARNED FACTS.
If CURRENT LEARNED FACTS is empty, then "updated_facts" will be the processed version of NEWLY OBSERVED POTENTIAL FACTS.

Respond ONLY with the JSON object.
"""

async def _curate_and_update_fact_store(newly_observed_facts: List[str]) -> bool:
    """
    Curates the fact store using an LLM by integrating newly observed facts
    with existing ones, then saves the updated fact store.

    Args:
        newly_observed_facts: A list of new potential facts to integrate.

    Returns:
        True if the curation and saving process was successful, False otherwise.
    """
    if not isinstance(newly_observed_facts, list) or not all(isinstance(f, str) for f in newly_observed_facts):
        print("Error (_curate_and_update_fact_store): newly_observed_facts must be a list of strings.")
        return False

    current_facts = load_learned_facts() # Load from persistent_memory.py

    try:
        current_facts_json = json.dumps(current_facts)
        new_potential_facts_json = json.dumps(newly_observed_facts)
    except TypeError:
        print("Error (_curate_and_update_fact_store): Could not serialize facts to JSON for LLM prompt.")
        return False

    prompt = FACT_CURATION_PROMPT_TEMPLATE.format(
        current_facts_json=current_facts_json,
        new_potential_facts_json=new_potential_facts_json
    )

    if is_debug_mode():
        print(f"[DEBUG KNOWLEDGE_TOOLS] Fact Curation Prompt (first 300 chars):\n{prompt[:300]}...")

    model_name = get_model_for_task("fact_management") # A new task type for config, or use "fact_extraction" / "reflection"
    if not model_name: # Fallback
        model_name = get_model_for_task("reflection")

    llm_response_str = await invoke_ollama_model_async(prompt, model_name=model_name)

    if not llm_response_str:
        print("Error (_curate_and_update_fact_store): LLM returned no response for fact curation.")
        return False

    if is_debug_mode():
        print(f"[DEBUG KNOWLEDGE_TOOLS] Raw LLM response for fact curation:\n'{llm_response_str}'")
    
    # Clean LLM output (remove markdown fences)
    cleaned_response = llm_response_str.strip()
    if cleaned_response.startswith("```json"):
        cleaned_response = cleaned_response.lstrip("```json").rstrip("```").strip()
    elif cleaned_response.startswith("```"):
        cleaned_response = cleaned_response.lstrip("```").rstrip("```").strip()

    try:
        parsed_response = json.loads(cleaned_response)
        if not isinstance(parsed_response, dict) or "updated_facts" not in parsed_response:
            print(f"Error (_curate_and_update_fact_store): LLM response for fact curation is not a dict with 'updated_facts'. Response: {cleaned_response}")
            return False
        
        updated_facts_list = parsed_response["updated_facts"]
        if not isinstance(updated_facts_list, list) or not all(isinstance(fact, str) for fact in updated_facts_list):
            print(f"Error (_curate_and_update_fact_store): 'updated_facts' from LLM is not a list of strings. Response: {parsed_response}")
            return False

        if save_learned_facts(updated_facts_list): # Save to persistent_memory.py
            print(f"Info (_curate_and_update_fact_store): Fact store updated and saved. Total facts: {len(updated_facts_list)}.")
            return True
        else:
            print("Error (_curate_and_update_fact_store): Failed to save curated facts.")
            return False

    except json.JSONDecodeError:
        print(f"Error (_curate_and_update_fact_store): Failed to parse LLM JSON response for fact curation. Response: {cleaned_response}")
        return False
    except Exception as e:
        print(f"Error (_curate_and_update_fact_store): Unexpected error during fact curation: {e}")
        return False

async def learn_fact(fact_text: str) -> str:
    """
    Learns a new fact by adding it to the knowledge base via LLM curation.
    This function is now asynchronous.

    Args:
        fact_text: The fact to be learned.

    Returns:
        A string confirming that the fact has been processed for learning,
        or an error message if the process failed.
    """
    if not isinstance(fact_text, str) or not fact_text.strip():
        return "Sorry, I can only learn non-empty facts provided as text."

    # For a single fact, wrap it in a list for the curation function
    success = await _curate_and_update_fact_store([fact_text])
    
    if success:
        # We can't be sure if *this specific fact* was added, modified, or discarded by the LLM.
        # The confirmation message indicates the process completed, including the save attempt.
        return f"Okay, I've processed the information: '{fact_text}'. My knowledge base has been updated and the changes were saved."
    else:
        # This now more clearly indicates a failure in the curation/save pipeline.
        return (f"Sorry, I encountered an error while trying to process and save the information: '{fact_text}'. "
                "The fact may not have been permanently learned.")

def recall_facts(query: Optional[str] = None) -> List[str]:
    """
    Retrieves a list of learned facts from the curated fact store.
    Can be filtered by an optional query string.

    Args:
        query: An optional keyword or phrase to filter facts.
               If omitted or empty, all facts are returned.
               The filter is case-insensitive.

    Returns:
        A list of strings, where each string is a learned fact matching the query.
        Returns all facts if no query is provided.
        Returns an empty list if no facts are stored or if no facts match the query.
    """
    all_facts = load_learned_facts() # From persistent_memory.py

    if not all_facts:
        return []

    if query and query.strip():
        query_lower = query.lower()
        return [fact for fact in all_facts if query_lower in fact.lower()]
    
    return all_facts

async def run_periodic_fact_store_curation_async() -> bool:
    """
    Performs a periodic curation of the entire fact store using the LLM.
    This is intended to be called by a background service.
    It calls the existing _curate_and_update_fact_store with no new facts,
    prompting the LLM to review and refine the current set of facts.

    Returns:
        True if the curation process was successful, False otherwise.
    """
    print("Info (run_periodic_fact_store_curation_async): Starting periodic fact store curation.")
    if is_debug_mode():
        current_facts_count = len(load_learned_facts())
        print(f"[DEBUG KNOWLEDGE_TOOLS] Periodic curation: {current_facts_count} facts before curation.")

    # Call curation with an empty list of new facts.
    # The LLM prompt is designed to consolidate/refine existing facts in this scenario.
    success = await _curate_and_update_fact_store([]) 

    if success:
        print("Info (run_periodic_fact_store_curation_async): Periodic fact store curation completed successfully.")
    else:
        print("Error (run_periodic_fact_store_curation_async): Periodic fact store curation failed.")
    return success

if __name__ == '__main__': # pragma: no cover
    import asyncio
    import os
    
    # --- Test Setup ---
    # This setup is for direct testing of this module.
    # In a real application, dependencies like Ollama and data files would be managed differently.
    if not os.path.exists("data"):
        os.makedirs("data", exist_ok=True)
    
    # Use a specific test facts file to avoid interfering with the main one
    # This assumes persistent_memory.py functions can accept a filepath override,
    # or we mock them to use this path. For simplicity, we'll assume
    # load_learned_facts and save_learned_facts in this module will use the default.
    # To test properly, we'd ideally mock persistent_memory's file path.
    # For this __main__, we'll just let it use the default `data/learned_facts.json`
    # and clean it up.

    # Backup original facts file if it exists
    DEFAULT_FACTS_FILE = "data/learned_facts.json"
    original_facts_content = None
    if os.path.exists(DEFAULT_FACTS_FILE):
        with open(DEFAULT_FACTS_FILE, 'r', encoding='utf-8') as f_orig:
            original_facts_content = f_orig.read()
    
    # Start with an empty facts file for tests
    save_learned_facts([]) 

    # Mock invoke_ollama_model_async for testing _curate_and_update_fact_store
    async def mock_curation_llm(prompt: str, model_name: str, **kwargs):
        print(f"\n--- MOCK FACT CURATION LLM CALLED (Model: {model_name}) ---")
        # print(f"Prompt received by mock LLM:\n{prompt}\n--------------------------")
        
        # Simple mock logic based on prompt content
        if '"The user\'s favorite color is blue."' in prompt and '"The user mentioned their favorite color is azure."' in prompt:
            return json.dumps({"updated_facts": ["The user's favorite color is azure (refined from blue).", "The capital of France is Paris."]})
        elif '"The capital of France is Paris."' in prompt and '"Paris is in France."' in prompt: # Test duplicate/redundancy
            return json.dumps({"updated_facts": ["The capital of France is Paris."]})
        elif '"The user asked about the weather today."' in prompt: # Test discarding transient
             return json.dumps({"updated_facts": ["The capital of France is Paris."]}) # Assume Paris was existing
        elif '"A new unique fact."' in prompt and not '"The capital of France is Paris."' in prompt: # Test adding new to empty
            return json.dumps({"updated_facts": ["A new unique fact."]})
        elif '"newly_observed_facts": "[]"' in prompt and '"current_facts_json": "[\"fact A\", \"fact A\", \"fact B\"]"' in prompt: # Test periodic de-duplication
            print("Mock LLM: Detected periodic de-duplication test.")
            return json.dumps({"updated_facts": ["fact A", "fact B"]})
        elif '"newly_observed_facts": "[]"' in prompt and '"current_facts_json": "[\"This is a very very very long and verbose fact that could be shorter.\", \"fact C\"]"' in prompt: # Test periodic refinement
            print("Mock LLM: Detected periodic refinement test.")
            return json.dumps({"updated_facts": ["Shorter fact.", "fact C"]})
        elif '"A new unique fact."' in prompt and '"The capital of France is Paris."' in prompt: # Test adding new to existing
            return json.dumps({"updated_facts": ["The capital of France is Paris.", "A new unique fact."]})
        
        # Default: return current facts if new facts are empty or no change
        current_facts_match = re.search(r"CURRENT LEARNED FACTS \(JSON list of strings\):\s*(\[.*?\])", prompt, re.DOTALL)
        if current_facts_match:
            try:
                current_facts_list = json.loads(current_facts_match.group(1))
                return json.dumps({"updated_facts": current_facts_list})
            except json.JSONDecodeError:
                pass # Fall through if parsing fails
        
        return json.dumps({"updated_facts": []}) # Fallback empty

    # Patch the LLM call within this module's scope for testing
    original_invoke_async = invoke_ollama_model_async
    # Need to assign to the name used within this module (knowledge_tools.py)
    globals()['invoke_ollama_model_async'] = mock_curation_llm


    async def run_knowledge_tool_tests():
        print("\n--- Testing learn_fact (with LLM Curation) ---")
        
        # Test 1: Learn a completely new fact (to an empty store)
        print("\nTest 1: Learn a new fact (empty store)")
        fact1 = "A new unique fact."
        result1 = await learn_fact(fact1)
        print(f"Learn fact 1 result: {result1}")
        assert "processed the information" in result1
        recalled1 = recall_facts()
        print(f"Recalled after fact 1: {recalled1}")
        assert fact1 in recalled1
        assert len(recalled1) == 1

        # Test 2: Learn another new fact (add to existing)
        print("\nTest 2: Learn another new fact")
        # First, ensure "The capital of France is Paris." is in the store for the mock to work as expected
        await _curate_and_update_fact_store(["The capital of France is Paris."]) # Prime the store
        
        fact2 = "A new unique fact." # This should be added alongside Paris fact by the mock
        result2 = await learn_fact(fact2) # This will trigger curation with ["Paris..."] and ["A new unique fact."]
        print(f"Learn fact 2 result: {result2}")
        assert "processed the information" in result2
        recalled2 = recall_facts()
        print(f"Recalled after fact 2: {recalled2}")
        assert "The capital of France is Paris." in recalled2
        assert fact2 in recalled2
        assert len(recalled2) == 2


        # Test 3: "Learn" a fact that refines an existing one
        print("\nTest 3: Learn a refining fact")
        # Current facts: ["The capital of France is Paris.", "A new unique fact."]
        # Let's try to refine "A new unique fact." to "A new unique fact has been refined."
        # The mock needs to handle this. Let's assume the mock for "The user's favorite color is blue."
        # and "The user mentioned their favorite color is azure." is a good proxy.
        # We'll set current facts to ["The user's favorite color is blue.", "The capital of France is Paris."]
        save_learned_facts(["The user's favorite color is blue.", "The capital of France is Paris."])
        
        fact3_refining = "The user mentioned their favorite color is azure."
        result3 = await learn_fact(fact3_refining)
        print(f"Learn fact 3 (refining) result: {result3}")
        assert "processed the information" in result3
        recalled3 = recall_facts()
        print(f"Recalled after fact 3: {recalled3}")
        assert "The user's favorite color is azure (refined from blue)." in recalled3
        assert "The capital of France is Paris." in recalled3
        assert len(recalled3) == 2


        # Test 4: "Learn" a duplicate fact
        print("\nTest 4: Learn a duplicate fact")
        # Current facts: ["The user's favorite color is azure (refined from blue).", "The capital of France is Paris."]
        fact4_duplicate = "The capital of France is Paris." # Already exists
        result4 = await learn_fact(fact4_duplicate) # Mock should handle this
        print(f"Learn fact 4 (duplicate) result: {result4}")
        assert "processed the information" in result4 # Still processed
        recalled4 = recall_facts()
        print(f"Recalled after fact 4: {recalled4}")
        assert len(recalled4) == 2 # Length should not change


        # Test 5: "Learn" a transient/non-factual piece of info
        print("\nTest 5: Learn transient info")
        # Current facts: ["The user's favorite color is azure (refined from blue).", "The capital of France is Paris."]
        fact5_transient = "The user asked about the weather today." # Mock should discard this
        result5 = await learn_fact(fact5_transient)
        print(f"Learn fact 5 (transient) result: {result5}")
        assert "processed the information" in result5
        recalled5 = recall_facts()
        print(f"Recalled after fact 5: {recalled5}")
        assert len(recalled5) == 2 # Length should not change, transient info discarded

        # Test 6: Empty fact
        print("\nTest 6: Learn empty fact")
        result6 = await learn_fact("   ")
        print(f"Learn fact 6 (empty) result: {result6}")
        assert "Sorry, I can only learn non-empty facts" in result6
        recalled6 = recall_facts()
        assert len(recalled6) == 2 # Length should not change

        print("\n--- Recall tests (already implicitly tested, but explicit checks) ---")
        # Current facts: ["The user's favorite color is azure (refined from blue).", "The capital of France is Paris."]
        all_facts = recall_facts()
        assert len(all_facts) == 2
        
        queried_facts = recall_facts("Paris")
        assert len(queried_facts) == 1
        assert "The capital of France is Paris." in queried_facts

        queried_facts_none = recall_facts("Berlin")
        assert len(queried_facts_none) == 0

        print("\n--- Testing run_periodic_fact_store_curation_async ---")
        # Test 1: Periodic curation with duplicates
        print("\nPeriodic Curation Test 1: De-duplication")
        save_learned_facts(["fact A", "fact A", "fact B"])
        periodic_result1 = await run_periodic_fact_store_curation_async()
        assert periodic_result1 is True
        recalled_periodic1 = recall_facts()
        print(f"Recalled after periodic de-duplication: {recalled_periodic1}")
        assert "fact A" in recalled_periodic1
        assert "fact B" in recalled_periodic1
        assert len(recalled_periodic1) == 2

        # Test 2: Periodic curation for refinement
        print("\nPeriodic Curation Test 2: Refinement")
        save_learned_facts(["This is a very very very long and verbose fact that could be shorter.", "fact C"])
        periodic_result2 = await run_periodic_fact_store_curation_async()
        assert periodic_result2 is True
        recalled_periodic2 = recall_facts()
        print(f"Recalled after periodic refinement: {recalled_periodic2}")
        assert "Shorter fact." in recalled_periodic2
        assert "fact C" in recalled_periodic2
        assert len(recalled_periodic2) == 2


        # Restore original LLM invoker
        globals()['invoke_ollama_model_async'] = original_invoke_async

        # Restore original facts file content or delete if it wasn't there
        print("\n--- Test Cleanup ---")
        if original_facts_content is not None:
            with open(DEFAULT_FACTS_FILE, 'w', encoding='utf-8') as f_restore:
                f_restore.write(original_facts_content)
            print(f"Restored original content to {DEFAULT_FACTS_FILE}")
        elif os.path.exists(DEFAULT_FACTS_FILE): 
            os.remove(DEFAULT_FACTS_FILE)
            print(f"Removed test file {DEFAULT_FACTS_FILE}")
        
        data_dir = os.path.dirname(DEFAULT_FACTS_FILE)
        if os.path.exists(data_dir) and not os.listdir(data_dir):
            try: os.rmdir(data_dir)
            except OSError: pass # Ignore if not empty or other issues

        print("\n--- Knowledge Tools (LLM Curation) Tests Finished ---")

    asyncio.run(run_knowledge_tool_tests())
