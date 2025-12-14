import json
from typing import Optional, List
from ai_assistant.memory.persistent_memory import load_learned_facts, save_learned_facts
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async
from ai_assistant.config import get_model_for_task, is_debug_mode
import re
FACT_CURATION_PROMPT_TEMPLATE = '\nYou are an AI Knowledge Base Curator. Your primary responsibility is to maintain a clean, accurate, and non-redundant set of learned facts.\nYou will be given the CURRENT set of learned facts and a list of NEWLY OBSERVED potential facts.\n\nYour task is to produce an UPDATED AND FINALIZED list of facts by performing the following operations:\n1.  **Integrate New Information:** If a newly observed item provides genuinely new and persistent factual information, add it. **Err on the side of KEEPING the new fact if you are unsure.**\n2.  **Consolidate & Refine:** If a new item is related to an existing fact but adds more detail, clarifies, or slightly corrects it, merge them into a single, more comprehensive fact. Prefer the more accurate or detailed version (usually the NEW one).\n3.  **Eliminate Duplicates:** If a new item is *strictly* identical or *completely* semantically equivalent to an existing fact, do not add the new item. Ensure the existing fact is retained if it\'s well-phrased.\n4.  **Correct/Update:** If a new item contradicts an older fact (e.g., "User\'s favorite color was blue" vs new "User\'s favorite color is now green"), **replace the older fact with the NEW one**. Assume the new information is the most current truth.\n5.  **Discard Non-Factual/Transient Info:** If a newly observed item is not a persistent fact (e.g., commands, questions, temporary states), discard it.\n    *   Examples to DISCARD: "run search", "what is weather?", "I am tired", "file is missing".\n    *   Examples to KEEP: "User\'s name is Alex.", "Project is \'hangman\'", "User prefers Python.", "Capital of France is Paris."\n6.  **Maintain Conciseness & Clarity:** Ensure facts are stated clearly. Rephrase if necessary.\n\nCURRENT LEARNED FACTS (JSON list of strings):\n{current_facts_json}\n\nNEWLY OBSERVED POTENTIAL FACTS (JSON list of strings):\n{new_potential_facts_json}\n\nBased on these inputs, output the UPDATED AND FINALIZED list of facts.\nThe output *MUST* be a JSON object containing a single key "updated_facts", which is a list of strings.\n\nIf NEWLY OBSERVED POTENTIAL FACTS is empty, or if after processing no changes are warranted to CURRENT LEARNED FACTS, then the "updated_facts" list should be identical to the CURRENT LEARNED FACTS.\nIf CURRENT LEARNED FACTS is empty, then "updated_facts" will be the processed version of NEWLY OBSERVED POTENTIAL FACTS.\n\nRespond ONLY with the JSON object.\n'

async def _curate_and_update_fact_store(newly_observed_facts: List[str]) -> bool:
    """
    Curates the fact store using an LLM by integrating newly observed facts
    with existing ones, then saves the updated fact store.

    Args:
        newly_observed_facts: A list of new potential facts to integrate.

    Returns:
        True if the curation and saving process was successful, False otherwise.
    """
    if not isinstance(newly_observed_facts, list) or not all((isinstance(f, str) for f in newly_observed_facts)):
        print('Error (_curate_and_update_fact_store): newly_observed_facts must be a list of strings.')
        return False
    current_facts = load_learned_facts()
    try:
        current_facts_json = json.dumps(current_facts)
        new_potential_facts_json = json.dumps(newly_observed_facts)
    except TypeError:
        print('Error (_curate_and_update_fact_store): Could not serialize facts to JSON for LLM prompt.')
        return False
    prompt = FACT_CURATION_PROMPT_TEMPLATE.format(current_facts_json=current_facts_json, new_potential_facts_json=new_potential_facts_json)
    if is_debug_mode():
        print(f'[DEBUG KNOWLEDGE_TOOLS] Fact Curation Prompt (first 300 chars):\n{prompt[:300]}...')
    model_name = get_model_for_task('fact_management')
    if not model_name:
        model_name = get_model_for_task('reflection')
    llm_response_str = await invoke_ollama_model_async(prompt, model_name=model_name)
    if not llm_response_str:
        print('Error (_curate_and_update_fact_store): LLM returned no response for fact curation.')
        return False
    if is_debug_mode():
        print(f"[DEBUG KNOWLEDGE_TOOLS] Raw LLM response for fact curation:\n'{llm_response_str}'")
    cleaned_response = llm_response_str.strip()
    if cleaned_response.startswith('```json'):
        cleaned_response = cleaned_response.lstrip('```json').rstrip('```').strip()
    elif cleaned_response.startswith('```'):
        cleaned_response = cleaned_response.lstrip('```').rstrip('```').strip()
    try:
        parsed_response = json.loads(cleaned_response)
        if not isinstance(parsed_response, dict) or 'updated_facts' not in parsed_response:
            print(f"Error (_curate_and_update_fact_store): LLM response for fact curation is not a dict with 'updated_facts'. Response: {cleaned_response}")
            return False
        updated_facts_list = parsed_response['updated_facts']
        final_facts_to_save: List[str] = []
        if isinstance(updated_facts_list, list):
            for item in updated_facts_list:
                if isinstance(item, str):
                    final_facts_to_save.append(item)
                elif isinstance(item, dict) and 'text' in item:
                    final_facts_to_save.append(item['text'])
                else:
                    pass
        else:
            print(f"Error (_curate_and_update_fact_store): 'updated_facts' from LLM is not a list. Response: {parsed_response}")
            return False
        if not final_facts_to_save and updated_facts_list:
            print(f"Warning (_curate_and_update_fact_store): parsed 'updated_facts' but extracted 0 strings. Check LLM output format. Response: {parsed_response}")
        if save_learned_facts(final_facts_to_save):
            print(f'Info (_curate_and_update_fact_store): Fact store updated and saved. Total facts: {len(final_facts_to_save)}.')
            return True
        else:
            print('Error (_curate_and_update_fact_store): Failed to save curated facts.')
            return False
    except json.JSONDecodeError:
        print(f'Error (_curate_and_update_fact_store): Failed to parse LLM JSON response for fact curation. Response: {cleaned_response}')
        return False
    except Exception as e:
        print(f'Error (_curate_and_update_fact_store): Unexpected error during fact curation: {e}')
        return False

async def learn_fact(fact: str) -> str:
    """
    Learns a new fact by adding it to the knowledge base via LLM curation.
    This function is now asynchronous.

    Args:
        fact: The fact to be learned.

    Returns:
        A string confirming that the fact has been processed for learning,
        or an error message if the process failed.
    """
    if not isinstance(fact, str) or not fact.strip():
        return 'Sorry, I can only learn non-empty facts provided as text.'
    try:
        success = await _curate_and_update_fact_store([fact])
    except Exception as e:
        return f"Sorry, I encountered an error while trying to process and save the information: '{fact}'. The fact may not have been permanently learned. Error details: {e}"
    if success:
        return f"Okay, I've processed the information: '{fact}'. My knowledge base has been updated and the changes were saved."
    else:
        return f"Sorry, I encountered an error while trying to process and save the information: '{fact}'. The fact may not have been permanently learned."

def recall_facts(query: Optional[str]=None) -> List[str]:
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
    all_facts = load_learned_facts()
    if not all_facts:
        return []
    if query:
        if isinstance(query, dict):
            query = query.get('query', query.get('text', str(query)))
        if not isinstance(query, str):
            query = str(query)
        if query.strip():
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
    print('Info (run_periodic_fact_store_curation_async): Starting periodic fact store curation.')
    if is_debug_mode():
        current_facts_count = len(load_learned_facts())
        print(f'[DEBUG KNOWLEDGE_TOOLS] Periodic curation: {current_facts_count} facts before curation.')
    success = await _curate_and_update_fact_store([])
    if success:
        print('Info (run_periodic_fact_store_curation_async): Periodic fact store curation completed successfully.')
    else:
        print('Error (run_periodic_fact_store_curation_async): Periodic fact store curation failed.')
    return success
if __name__ == '__main__':
    import asyncio
    import os
    if not os.path.exists('data'):
        os.makedirs('data', exist_ok=True)
    DEFAULT_FACTS_FILE = 'data/learned_facts.json'
    original_facts_content = None
    if os.path.exists(DEFAULT_FACTS_FILE):
        with open(DEFAULT_FACTS_FILE, 'r', encoding='utf-8') as f_orig:
            original_facts_content = f_orig.read()
    save_learned_facts([])

    async def mock_curation_llm(prompt: str, model_name: str, **kwargs):
        print(f'\n--- MOCK FACT CURATION LLM CALLED (Model: {model_name}) ---')
        if '"The user\'s favorite color is blue."' in prompt and '"The user mentioned their favorite color is azure."' in prompt:
            return json.dumps({'updated_facts': ["The user's favorite color is azure (refined from blue).", 'The capital of France is Paris.']})
        elif '"The capital of France is Paris."' in prompt and '"Paris is in France."' in prompt:
            return json.dumps({'updated_facts': ['The capital of France is Paris.']})
        elif '"The user asked about the weather today."' in prompt:
            return json.dumps({'updated_facts': ['The capital of France is Paris.']})
        elif '"A new unique fact."' in prompt and (not '"The capital of France is Paris."' in prompt):
            return json.dumps({'updated_facts': ['A new unique fact.']})
        elif '"newly_observed_facts": "[]"' in prompt and '"current_facts_json": "["fact A", "fact A", "fact B"]"' in prompt:
            print('Mock LLM: Detected periodic de-duplication test.')
            return json.dumps({'updated_facts': ['fact A', 'fact B']})
        elif '"newly_observed_facts": "[]"' in prompt and '"current_facts_json": "["This is a very very very long and verbose fact that could be shorter.", "fact C"]"' in prompt:
            print('Mock LLM: Detected periodic refinement test.')
            return json.dumps({'updated_facts': ['Shorter fact.', 'fact C']})
        elif '"A new unique fact."' in prompt and '"The capital of France is Paris."' in prompt:
            return json.dumps({'updated_facts': ['The capital of France is Paris.', 'A new unique fact.']})
        current_facts_match = re.search('CURRENT LEARNED FACTS \\(JSON list of strings\\):\\s*(\\[.*?\\])', prompt, re.DOTALL)
        if current_facts_match:
            try:
                current_facts_list = json.loads(current_facts_match.group(1))
                return json.dumps({'updated_facts': current_facts_list})
            except json.JSONDecodeError:
                pass
        return json.dumps({'updated_facts': []})
    original_invoke_async = invoke_ollama_model_async
    globals()['invoke_ollama_model_async'] = mock_curation_llm

    async def run_knowledge_tool_tests():
        print('\n--- Testing learn_fact (with LLM Curation) ---')
        print('\nTest 1: Learn a new fact (empty store)')
        fact1 = 'A new unique fact.'
        result1 = await learn_fact(fact1)
        print(f'Learn fact 1 result: {result1}')
        assert 'processed the information' in result1
        recalled1 = recall_facts()
        print(f'Recalled after fact 1: {recalled1}')
        assert fact1 in recalled1
        assert len(recalled1) == 1
        print('\nTest 2: Learn another new fact')
        await _curate_and_update_fact_store(['The capital of France is Paris.'])
        fact2 = 'A new unique fact.'
        result2 = await learn_fact(fact2)
        print(f'Learn fact 2 result: {result2}')
        assert 'processed the information' in result2
        recalled2 = recall_facts()
        print(f'Recalled after fact 2: {recalled2}')
        assert 'The capital of France is Paris.' in recalled2
        assert fact2 in recalled2
        assert len(recalled2) == 2
        print('\nTest 3: Learn a refining fact')
        save_learned_facts(["The user's favorite color is blue.", 'The capital of France is Paris.'])
        fact3_refining = 'The user mentioned their favorite color is azure.'
        result3 = await learn_fact(fact3_refining)
        print(f'Learn fact 3 (refining) result: {result3}')
        assert 'processed the information' in result3
        recalled3 = recall_facts()
        print(f'Recalled after fact 3: {recalled3}')
        assert "The user's favorite color is azure (refined from blue)." in recalled3
        assert 'The capital of France is Paris.' in recalled3
        assert len(recalled3) == 2
        print('\nTest 4: Learn a duplicate fact')
        fact4_duplicate = 'The capital of France is Paris.'
        result4 = await learn_fact(fact4_duplicate)
        print(f'Learn fact 4 (duplicate) result: {result4}')
        assert 'processed the information' in result4
        recalled4 = recall_facts()
        print(f'Recalled after fact 4: {recalled4}')
        assert len(recalled4) == 2
        print('\nTest 5: Learn transient info')
        fact5_transient = 'The user asked about the weather today.'
        result5 = await learn_fact(fact5_transient)
        print(f'Learn fact 5 (transient) result: {result5}')
        assert 'processed the information' in result5
        recalled5 = recall_facts()
        print(f'Recalled after fact 5: {recalled5}')
        assert len(recalled5) == 2
        print('\nTest 6: Learn empty fact')
        result6 = await learn_fact('   ')
        print(f'Learn fact 6 (empty) result: {result6}')
        assert 'Sorry, I can only learn non-empty facts' in result6
        recalled6 = recall_facts()
        assert len(recalled6) == 2
        print('\n--- Recall tests (already implicitly tested, but explicit checks) ---')
        all_facts = recall_facts()
        assert len(all_facts) == 2
        queried_facts = recall_facts('Paris')
        assert len(queried_facts) == 1
        assert 'The capital of France is Paris.' in queried_facts
        queried_facts_none = recall_facts('Berlin')
        assert len(queried_facts_none) == 0
        print('\n--- Testing run_periodic_fact_store_curation_async ---')
        print('\nPeriodic Curation Test 1: De-duplication')
        save_learned_facts(['fact A', 'fact A', 'fact B'])
        periodic_result1 = await run_periodic_fact_store_curation_async()
        assert periodic_result1 is True
        recalled_periodic1 = recall_facts()
        print(f'Recalled after periodic de-duplication: {recalled_periodic1}')
        assert 'fact A' in recalled_periodic1
        assert 'fact B' in recalled_periodic1
        assert len(recalled_periodic1) == 2
        print('\nPeriodic Curation Test 2: Refinement')
        save_learned_facts(['This is a very very very long and verbose fact that could be shorter.', 'fact C'])
        periodic_result2 = await run_periodic_fact_store_curation_async()
        assert periodic_result2 is True
        recalled_periodic2 = recall_facts()
        print(f'Recalled after periodic refinement: {recalled_periodic2}')
        assert 'Shorter fact.' in recalled_periodic2
        assert 'fact C' in recalled_periodic2
        assert len(recalled_periodic2) == 2
        globals()['invoke_ollama_model_async'] = original_invoke_async
        print('\n--- Test Cleanup ---')
        if original_facts_content is not None:
            with open(DEFAULT_FACTS_FILE, 'w', encoding='utf-8') as f_restore:
                f_restore.write(original_facts_content)
            print(f'Restored original content to {DEFAULT_FACTS_FILE}')
        elif os.path.exists(DEFAULT_FACTS_FILE):
            os.remove(DEFAULT_FACTS_FILE)
            print(f'Removed test file {DEFAULT_FACTS_FILE}')
        data_dir = os.path.dirname(DEFAULT_FACTS_FILE)
        if os.path.exists(data_dir) and (not os.listdir(data_dir)):
            try:
                os.rmdir(data_dir)
            except OSError:
                pass
        print('\n--- Knowledge Tools (LLM Curation) Tests Finished ---')
    asyncio.run(run_knowledge_tool_tests())