# System Evolution Roadmap

This document outlines planned enhancements, optimizations, and future capabilities for the AI Assistant Cockpit, prioritized based on system resilience, autonomy, and efficiency.

---

## 1. ~~Planning: Upgrade from Keyword Matching to True RAG Semantic Search~~ (Completed)
~~In `hierarchical_planner.py`, the `_retrieve_relevant_facts` function currently relies on a naive Python set-intersection (keyword matching) to find context for the LLM.~~
* ~~**The Fix:** Inject the existing `RAGSystem` (`rag_system.py`) into the `HierarchicalPlanner` and replace the keyword matching logic with a call to `await self.rag_system.retrieve_context(query)`. This will allow the planner to use vector embeddings to find semantically related facts.~~

## 2. ~~Execution: Iterative Council Debate (Instead of Binary Reject)~~ (Completed)
~~In `action_executor.py`, within the `_apply_test_and_revert_code` method, the "Council" is initiated to review proposed code.~~
* ~~**The Fix:** Implement an Iterative Refinement Loop. If the Skeptic finds a bug, instead of abandoning the attempt, pass the reasoning (the Skeptic's critique) directly back to the `CodeService` as a specific instruction to generate a new revision. Allow the AI to try passing the Council 2 or 3 times before giving up. This mimics how a real developer responds to a PR review.~~

## 3. ~~Memory: Consolidate LLM Calls for Fact Ingestion~~ (Completed)
~~In `action_executor.py`, when handling the `ADD_LEARNED_FACT` action, the system makes two separate LLM calls (`_is_fact_valuable(fact)` and `_get_fact_category_with_llm(fact)`).~~
* ~~**The Fix:** Combine the prompts. Create a single `_assess_and_categorize_fact` method that asks the LLM to output a unified JSON schema: `{"is_valuable": true, "reason": "...", "category": "user_preference"}`.~~

## 4. ~~Architecture: Stateful "Heat Map" for the Evolutionary Architect~~ (Completed)
~~In `evolutionary_architect.py`, the `perform_architectural_audit` function uses a roulette wheel (`random.choices`) to pick a random file from your directories to audit.~~
* ~~**The Fix:** Implement a "Codebase Heatmap" JSON ledger. When a file is audited and passed by the `StaticAnalysisFilter`, record the `last_audited_timestamp`. When picking files, weight the random selection heavily toward files that have been modified since their last audit, or files that haven't been audited in weeks.~~

## 5. ~~Memory: Batch Vector Embedding Syncing~~ (Completed)
~~In `rag_system.py`, the `sync_existing_facts` function iterates through a list of dictionaries and calls `await self.ingest_fact(text)` on each one sequentially.~~
* ~~**The Fix:** Add a `get_embeddings_batch_async(texts)` method to the LLM provider (if supported) or use `asyncio.gather()` to fetch the embeddings concurrently in chunks of 10-20 before passing them to ChromaDB.~~

## 6. ~~Planning: DAG (Directed Acyclic Graph) Execution~~ (Completed)
~~In `hierarchical_planner.py`, a `depends_on` field is currently generated for tasks, advising the LLM to output filenames like `["app.py"]`.~~
* ~~**The Fix:** Instruct the LLM in `LLM_HP_STEP_ELABORATION_PROMPT_TEMPLATE` to use the actual `step_id` (e.g., `["1.1", "1.2"]`) for the `depends_on` field. Once the planner returns the JSON, use a library like `networkx` or a simple topological sort to execute tasks completely concurrently via `asyncio.gather` for tasks that share no dependencies, massively speeding up the time it takes the AI to scaffold a project.~~

---

*(Previously completed items (Quarantine State, Token Governance, Desktop Scope Guardrails, Visual Audits, etc.) have been removed from this roadmap as they are successfully implemented and deployed in the main branch).*