import os
import asyncio
import logging

# Adjust path to import ai_assistant modules
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_assistant.memory.rag_system import RAGSystem
from ai_assistant.llm_interface.ollama_client import OllamaProvider, invoke_ollama_model_async
from ai_assistant.memory.persistent_memory import load_learned_facts, save_learned_facts

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

CONSOLIDATION_PROMPT = """
You are an expert Data Deduplication Agent.
I have a list of facts that are semantically similar. I need you to consolidate them into a SINGLE, comprehensive, and accurate fact.

FACTS TO MERGE:
{facts_list}

INSTRUCTIONS:
1. Identify the core truth across these facts.
2. Merge details (e.g., specific location + general state -> specific location, state).
3. Resolve conflicts by preferring the most specific or recent-looking information.
4. Output ONLY the single merged fact as a string. Do not output JSON.
"""

async def clean_memory():
    logger.info("Starting memory cleanup process...")

    # 1. Load all current facts
    current_facts = load_learned_facts()
    if not current_facts:
        logger.info("No facts found in learned_facts.json.")
        return

    logger.info(f"Loaded {len(current_facts)} facts.")

    # 2. Initialize RAG system
    provider = OllamaProvider()
    rag = RAGSystem(provider)

    # Check if Chroma is empty. If not, we might want to clear it or we assume it matches JSON.
    # For a "One-Time Fix", it's safer to clear Chroma and re-populate from the cleaned JSON.
    # But first we need to group the JSON facts.

    # We will use embeddings to group facts.
    logger.info("Generating embeddings for clustering...")

    # To group, we can use a simple approach:
    # - Iterate through facts.
    # - Maintain a list of clusters (each cluster has a representative embedding).
    # - If fact is close to a cluster, add to it. Else create new cluster.

    clusters = [] # List of {'facts': [str], 'embedding': np.array}

    import numpy as np

    for i, fact in enumerate(current_facts):
        if i % 10 == 0:
            logger.info(f"Processing fact {i+1}/{len(current_facts)}...")

        embedding = await provider.get_embeddings_async(fact)
        if not embedding:
            continue

        embedding_np = np.array(embedding)
        norm = np.linalg.norm(embedding_np)
        if norm > 0:
            embedding_np = embedding_np / norm

        # Check against existing clusters
        best_cluster_idx = -1
        best_sim = -1.0

        for idx, cluster in enumerate(clusters):
            # Compare with representative (centroid or first item)
            # using first item for simplicity
            cluster_emb = cluster['embedding']
            sim = np.dot(embedding_np, cluster_emb)

            if sim > best_sim:
                best_sim = sim
                best_cluster_idx = idx

        THRESHOLD = 0.85 # High similarity threshold

        if best_sim > THRESHOLD:
            clusters[best_cluster_idx]['facts'].append(fact)
            # Update representative embedding? (Moving average)
            # Simple: keep first one as anchor.
        else:
            clusters.append({'facts': [fact], 'embedding': embedding_np})

    logger.info(f"Grouped {len(current_facts)} facts into {len(clusters)} clusters.")

    # 3. Process clusters
    cleaned_facts = []

    for cluster in clusters:
        facts = cluster['facts']
        if len(facts) == 1:
            cleaned_facts.append(facts[0])
        else:
            # Duplicate/Similar detected
            logger.info(f"Consolidating cluster of {len(facts)} facts: {facts}")
            facts_str = "\n".join([f"- {f}" for f in facts])
            prompt = CONSOLIDATION_PROMPT.replace("{facts_list}", facts_str)

            merged_fact = await invoke_ollama_model_async(prompt, model_name="llama3") # Use a good model
            merged_fact = merged_fact.strip().strip('"')

            logger.info(f"Merged into: {merged_fact}")
            cleaned_facts.append(merged_fact)

    # 4. Save cleaned list back to JSON
    logger.info(f"Saving {len(cleaned_facts)} cleaned facts to learned_facts.json...")
    save_learned_facts(cleaned_facts)

    # 5. Re-index into Chroma
    # First, we need to clear existing Chroma collection to ensure sync.
    # VectorStore doesn't expose "reset", but we can delete the collection or iterate and delete.
    # Since we are using persistent client, we can delete the collection.

    try:
        rag.vector_store.client.delete_collection("learned_facts")
        # Re-create
        rag.vector_store.collection = rag.vector_store.client.create_collection(
            name="learned_facts",
            metadata={"hnsw:space": "cosine"}
        )
        logger.info("Cleared existing Chroma collection.")
    except Exception as e:
        logger.warning(f"Could not clear collection (might not exist): {e}")

    logger.info("Re-indexing facts into ChromaDB...")
    await rag.sync_existing_facts(cleaned_facts) # This now takes list of strings? No, wait.
    # sync_existing_facts expects list of dicts or just strings?
    # RAGSystem.sync_existing_facts signature: (self, all_facts: List[Dict[str, Any]])
    # But wait, I passed `current_facts` (List[str]) to it before?
    # Let's check rag_system.py again.

    # In my updated rag_system.py:
    # async def sync_existing_facts(self, all_facts: List[Dict[str, Any]]):
    #     for fact in all_facts:
    #         text = fact.get("text")

    # This expects a list of DICTS.
    # `load_learned_facts()` returns a list of STRINGS (mostly).
    # `persistent_memory.load_learned_facts` returns List[str].

    # I need to adapt the data structure.
    facts_dicts = [{"text": f} for f in cleaned_facts]
    await rag.sync_existing_facts(facts_dicts)

    logger.info("Memory cleanup complete.")

if __name__ == "__main__":
    asyncio.run(clean_memory())
