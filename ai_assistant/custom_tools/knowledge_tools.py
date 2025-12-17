from typing import List, Optional
from typing import List, Optional

import json
import logging
import asyncio
from typing import Optional, List, Dict
from ai_assistant.memory.persistent_memory import load_learned_facts, save_learned_facts
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async
from ai_assistant.config import get_model_for_task, is_debug_mode
from ai_assistant.core.memory_manager import MemoryManager
logger = logging.getLogger(__name__)
FACT_DECISION_PROMPT_TEMPLATE = '\nYou are an AI Knowledge Base Curator. I want to add a new fact to my memory.\nCheck against the similar existing facts provided below and decide what to do.\n\nNEW FACT: "{new_fact}"\n\nSIMILAR EXISTING FACTS:\n{similar_facts_list}\n\nOPTIONS:\nA) ADD: The new fact is unique and contains new information not present in the existing facts.\nB) UPDATE: The new fact updates, corrects, or adds significant detail to an existing fact. (Specify which existing fact to replace).\nC) DISCARD: The new fact is already covered by the existing facts (duplicate) or is not worth saving (trivial/transient).\n\nResponse format:\nJSON object with keys:\n- "decision": "ADD", "UPDATE", or "DISCARD"\n- "reason": "Explanation..."\n- "target_id": "ID of the fact to update/replace" (Only for UPDATE, otherwise null)\n- "merged_fact": "The new merged text" (Only for UPDATE, otherwise null)\n\nRespond ONLY with the JSON object.\n'
_rag_system_cache = None

async def _get_rag_system():
    """
    Helper to get the RAG system.
    Uses a simple cache to avoid re-instantiation overhead.
    """
    global _rag_system_cache
    if _rag_system_cache:
        return _rag_system_cache
    from ai_assistant.llm_interface.ollama_client import OllamaProvider
    from ai_assistant.memory.rag_system import RAGSystem
    try:
        provider = OllamaProvider()
        rag = RAGSystem(provider)
        _rag_system_cache = rag
        return rag
    except Exception as e:
        logger.error(f'Failed to initialize RAG system: {e}')
        raise

async def _curate_and_update_fact_store(newly_observed_facts: List[str]) -> bool:
    """
    Curates the fact store by checking each new fact against the RAG system
    before adding it.

    Args:
        newly_observed_facts: A list of new potential facts to integrate.

    Returns:
        True if the process completed (even if some facts were discarded).
    """
    if not isinstance(newly_observed_facts, list) or not all((isinstance(f, str) for f in newly_observed_facts)):
        logger.error('newly_observed_facts must be a list of strings.')
        return False
    rag_system = await _get_rag_system()
    current_facts_list = load_learned_facts()
    facts_changed = False
    for new_fact in newly_observed_facts:
        if not new_fact.strip():
            continue
        similar_results = await rag_system.retrieve_context(new_fact, k=3, score_threshold=0.5)
        if not similar_results:
            logger.info(f'Adding new unique fact: {new_fact}')
            current_facts_list.append(new_fact)
            await rag_system.ingest_fact(new_fact, metadata={'source': 'autonomous_learning'})
            facts_changed = True
            continue
        similar_facts_display = []
        for res in similar_results:
            fact_id = res.get('id') or rag_system.generate_id(res['text'])
            similar_facts_display.append(f"ID: {fact_id} | Text: {res['text']}")
        similar_facts_str = '\n'.join(similar_facts_display)
        prompt = FACT_DECISION_PROMPT_TEMPLATE.replace('{new_fact}', new_fact).replace('{similar_facts_list}', similar_facts_str)
        model_name = get_model_for_task('fact_management') or get_model_for_task('reasoning')
        response_str = await invoke_ollama_model_async(prompt, model_name=model_name)
        try:
            cleaned_response = response_str.strip()
            if cleaned_response.startswith('```json'):
                cleaned_response = cleaned_response.lstrip('```json').rstrip('```').strip()
            elif cleaned_response.startswith('```'):
                cleaned_response = cleaned_response.lstrip('```').rstrip('```').strip()
            decision_data = json.loads(cleaned_response)
            decision = decision_data.get('decision', 'DISCARD').upper()
            if decision == 'ADD':
                logger.info(f'Decision ADD: {new_fact}')
                current_facts_list.append(new_fact)
                await rag_system.ingest_fact(new_fact, metadata={'source': 'autonomous_learning'})
                facts_changed = True
            elif decision == 'UPDATE':
                target_id = decision_data.get('target_id')
                merged_fact = decision_data.get('merged_fact')
                if target_id and merged_fact:
                    logger.info(f'Decision UPDATE: replacing {target_id} with {merged_fact}')
                    rag_system.delete_fact_by_id(target_id)
                    await rag_system.ingest_fact(merged_fact, metadata={'source': 'autonomous_learning_update'})
                    target_text = None
                    for res in similar_results:
                        res_id = res.get('id') or rag_system.generate_id(res['text'])
                        if res_id == target_id:
                            target_text = res['text']
                            break
                    if target_text and target_text in current_facts_list:
                        current_facts_list.remove(target_text)
                    current_facts_list.append(merged_fact)
                    facts_changed = True
                else:
                    logger.warning(f'UPDATE decision missing target_id or merged_fact. Discarding update.')
            elif decision == 'DISCARD':
                logger.info(f'Decision DISCARD: {new_fact}')
        except json.JSONDecodeError:
            logger.error(f'Failed to parse decision JSON: {cleaned_response}')
        except Exception as e:
            logger.error(f'Error processing fact decision: {e}')
    if facts_changed:
        save_learned_facts(current_facts_list)
    return True

async def learn_fact(fact: str) -> str:
    """
    Learns a new fact by adding it to the knowledge base via LLM curation.
    """
    if not isinstance(fact, str) or not fact.strip():
        return 'Sorry, I can only learn non-empty facts provided as text.'
    try:
        success = await _curate_and_update_fact_store([fact])
    except Exception as e:
        return f"Sorry, I encountered an error while trying to process and save the information: '{fact}'. Error: {e}"
    if success:
        return f"Okay, I've processed the information: '{fact}'."
    else:
        return f'Sorry, I could not process the fact.'

def recall_facts(query: Optional[str]=None) -> List[str]:
    """
    Retrieves a list of learned facts.
    Now uses RAG for query if provided, otherwise returns all from JSON.
    """
    from ai_assistant.memory.persistent_memory import load_learned_facts
    if not query:
        return load_learned_facts()
    all_facts = load_learned_facts()
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
    Placeholder for periodic curation.
    The new 'Check-before-Add' logic reduces the need for this,
    but a full cleanup script is better for periodic maintenance.
    """
    print('Periodic curation is now handled by the cleanup script logic.')
    return True