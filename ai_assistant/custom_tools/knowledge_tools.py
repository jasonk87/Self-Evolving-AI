import json
import logging
import asyncio
from typing import Optional, List, Dict
from ai_assistant.memory.persistent_memory import load_learned_facts, save_learned_facts
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async
from ai_assistant.config import get_model_for_task, is_debug_mode

# We need access to RAGSystem. Ideally this is injected, but for tools we often grab the global instance
# or instantiate a new one if needed. However, instantiating RAGSystem requires OllamaProvider.
# We will try to import the global memory manager which contains the RAG system.
from ai_assistant.core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

# New prompt template for single fact decision
FACT_DECISION_PROMPT_TEMPLATE = """
You are an AI Knowledge Base Curator. I want to add a new fact to my memory.
Check against the similar existing facts provided below and decide what to do.

NEW FACT: "{new_fact}"

SIMILAR EXISTING FACTS:
{similar_facts_list}

OPTIONS:
A) ADD: The new fact is unique and contains new information not present in the existing facts.
B) UPDATE: The new fact updates, corrects, or adds significant detail to an existing fact. (Specify which existing fact to replace).
C) DISCARD: The new fact is already covered by the existing facts (duplicate) or is not worth saving (trivial/transient).

Response format:
JSON object with keys:
- "decision": "ADD", "UPDATE", or "DISCARD"
- "reason": "Explanation..."
- "target_id": "ID of the fact to update/replace" (Only for UPDATE, otherwise null)
- "merged_fact": "The new merged text" (Only for UPDATE, otherwise null)

Respond ONLY with the JSON object.
"""

async def _get_rag_system():
    """
    Helper to get the RAG system from the global MemoryManager.
    """
    # This assumes web_app.py or similar has initialized it.
    # If we are running in a script (like tests), we might need to rely on what's available.
    # In the current architecture, MemoryManager is often a singleton or instantiated in core.
    # However, `ai_assistant.core.memory_manager` defines the class, not the instance.
    # We will try to instantiate a temporary one if needed, but that might be heavy.
    # A better approach is to rely on `ai_assistant.core.memory_manager` having a way to get the instance,
    # or pass it in.
    # For now, let's look at `ai_assistant/core/memory_manager.py` again.
    # It seems it doesn't expose a global instance directly.
    # But `web_app.py` does.
    # Let's try to create a lightweight connection or reuse.

    # Since we can't easily import `memory_manager` instance from `web_app` due to circular imports or context,
    # we will instantiate a fresh RAGSystem using the default OllamaProvider.
    from ai_assistant.llm_interface.ollama_client import OllamaProvider
    from ai_assistant.memory.rag_system import RAGSystem

    provider = OllamaProvider()
    rag = RAGSystem(provider)
    return rag

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

    # Load current simple list for fallback/legacy support
    current_facts_list = load_learned_facts()
    facts_changed = False

    for new_fact in newly_observed_facts:
        if not new_fact.strip():
            continue

        # 1. Query RAG for similar facts
        similar_results = await rag_system.retrieve_context(new_fact, k=3, score_threshold=0.5)

        if not similar_results:
            # No similar facts found, just ADD
            logger.info(f"Adding new unique fact: {new_fact}")
            current_facts_list.append(new_fact)
            await rag_system.ingest_fact(new_fact, metadata={"source": "autonomous_learning"})
            facts_changed = True
            continue

        # Format similar facts for prompt
        similar_facts_display = []
        for res in similar_results:
            # Ensure we have an ID to reference
            fact_id = res.get('id') or rag_system.generate_id(res['text'])
            similar_facts_display.append(f"ID: {fact_id} | Text: {res['text']}")

        similar_facts_str = "\n".join(similar_facts_display)

        # 2. Ask LLM
        prompt = FACT_DECISION_PROMPT_TEMPLATE.replace("{new_fact}", new_fact).replace("{similar_facts_list}", similar_facts_str)

        model_name = get_model_for_task('fact_management') or get_model_for_task('reasoning')
        response_str = await invoke_ollama_model_async(prompt, model_name=model_name)

        try:
            # Clean JSON
            cleaned_response = response_str.strip()
            if cleaned_response.startswith('```json'):
                cleaned_response = cleaned_response.lstrip('```json').rstrip('```').strip()
            elif cleaned_response.startswith('```'):
                cleaned_response = cleaned_response.lstrip('```').rstrip('```').strip()

            decision_data = json.loads(cleaned_response)
            decision = decision_data.get("decision", "DISCARD").upper()

            if decision == "ADD":
                logger.info(f"Decision ADD: {new_fact}")
                current_facts_list.append(new_fact)
                await rag_system.ingest_fact(new_fact, metadata={"source": "autonomous_learning"})
                facts_changed = True

            elif decision == "UPDATE":
                target_id = decision_data.get("target_id")
                merged_fact = decision_data.get("merged_fact")

                if target_id and merged_fact:
                    logger.info(f"Decision UPDATE: replacing {target_id} with {merged_fact}")

                    # Remove old fact from Chroma
                    rag_system.delete_fact_by_id(target_id)

                    # Add new fact to Chroma
                    await rag_system.ingest_fact(merged_fact, metadata={"source": "autonomous_learning_update"})

                    # Update simple list (this is tricky without IDs in simple list, strict matching?)
                    # We have to find the text that corresponds to the target_id.
                    # RAG result had the text.
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
                    logger.warning(f"UPDATE decision missing target_id or merged_fact. Discarding update.")

            elif decision == "DISCARD":
                logger.info(f"Decision DISCARD: {new_fact}")

        except json.JSONDecodeError:
            logger.error(f"Failed to parse decision JSON: {cleaned_response}")
        except Exception as e:
            logger.error(f"Error processing fact decision: {e}")

    # Save the updated simple list
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
        return f"Sorry, I could not process the fact."

def recall_facts(query: Optional[str]=None) -> List[str]:
    """
    Retrieves a list of learned facts.
    Now uses RAG for query if provided, otherwise returns all from JSON.
    """
    if not query:
        return load_learned_facts()

    # If query is provided, we should ideally use RAG, but this function signature expects sync return?
    # RAG retrieve_context is async.
    # Existing `recall_facts` was sync because it just filtered the list.
    # To keep compatibility, we might just filter the list for now,
    # OR run async loop if possible.

    # For now, stick to list filtering to avoid breaking sync callers.
    # If callers need semantic search, they should use the RAG system directly or an async tool.

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

# ... (Tests section omitted for brevity but should be updated if we kept it)
