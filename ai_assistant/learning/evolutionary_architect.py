import os
import ast
import random
import json
import logging
import re
from typing import Optional, Dict, Any
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async
from ai_assistant.config import get_model_for_task

logger = logging.getLogger(__name__)

# Categories and their weights for the roulette wheel
TARGET_CATEGORIES = {
    "core": {"path": "ai_assistant/core", "weight": 0.2},
    "custom_tools": {"path": "ai_assistant/custom_tools", "weight": 0.4},
    "intelligence": {"paths": ["ai_assistant/planning", "ai_assistant/learning"], "weight": 0.2},
    "projects": {"path": "ai_generated_projects", "weight": 0.2},
}

class StaticAnalysisFilter:
    """Filters files based on static analysis metrics."""

    @staticmethod
    def analyze_file(filepath: str, content: str) -> Dict[str, Any]:
        """
        Analyzes a file using AST and heuristics.
        Returns a dict with 'accept' (bool) and 'reason' (str) and 'metric' (str).
        """
        if len(content.splitlines()) < 50:
            return {"accept": False, "reason": "File too short (< 50 lines)."}

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return {"accept": False, "reason": "SyntaxError during parsing."}

        # Complexity check (simple heuristic: count decision points)
        complexity = 0
        for node in ast.walk(tree):
            if isinstance(node, (ast.If, ast.For, ast.While, ast.ExceptHandler, ast.With)):
                complexity += 1

        # Comment check (case-insensitive)
        has_todo = bool(re.search(r"\b(todo|fixme|hack)\b", content, re.IGNORECASE))

        # Deprecated pattern check
        has_deprecated = "os.system" in content or "shell=True" in content

        if complexity > 20:
             return {"accept": True, "reason": "High complexity detected.", "metric": "complexity"}

        if has_todo:
            return {"accept": True, "reason": "TODOs/FIXMEs detected.", "metric": "todo"}

        if has_deprecated:
            return {"accept": True, "reason": "Potential deprecated patterns detected.", "metric": "deprecated"}

        return {"accept": False, "reason": "File seems clean/simple."}

async def generate_evolution_proposal(filepath: str, content: str, analysis_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generates an evolution proposal using an LLM based on the analysis result.
    """
    metric = analysis_result.get("metric")

    prompt = ""
    lens = ""

    if metric == "complexity":
        lens = "The Optimizer Lens"
        prompt = (
            f"You are the 'Evolutionary Architect'. You are auditing the file `{filepath}`.\n"
            f"The static analysis detected HIGH COMPLEXITY (score: high).\n"
            f"Your goal: Propose a refactor to simplify logic, improve readability, or enhance performance (O(n)).\n"
            f"Focus on the most complex functions.\n\n"
            f"File Content:\n```python\n{content}\n```\n\n"
            f"Output a JSON object with keys: 'summary', 'plan', 'diff' (optional, concept diff).\n"
            f"IMPORTANT: Ensure the JSON is valid. Escape backslashes in strings (e.g., use \\\\n for newlines in code strings)."
        )
    elif metric == "deprecated":
        lens = "The Modernizer Lens"
        prompt = (
            f"You are the 'Evolutionary Architect'. You are auditing the file `{filepath}`.\n"
            f"The static analysis detected POTENTIALLY DEPRECATED or UNSAFE PATTERNS (e.g., os.system, shell=True).\n"
            f"Your goal: Propose a modern, safer alternative using `subprocess`, `shlex`, or other modern libraries.\n\n"
            f"File Content:\n```python\n{content}\n```\n\n"
            f"Output a JSON object with keys: 'summary', 'plan', 'diff' (optional, concept diff).\n"
            f"IMPORTANT: Ensure the JSON is valid. Escape backslashes in strings (e.g., use \\\\n for newlines in code strings)."
        )
    elif metric == "todo":
        lens = "The Completion Lens"
        prompt = (
            f"You are the 'Evolutionary Architect'. You are auditing the file `{filepath}`.\n"
            f"The static analysis detected TODOs, FIXMEs, or HACKs.\n"
            f"Your goal: Propose code to implement the missing functionality or clean up the hack.\n\n"
            f"File Content:\n```python\n{content}\n```\n\n"
            f"Output a JSON object with keys: 'summary', 'plan', 'diff' (optional, concept diff).\n"
            f"IMPORTANT: Ensure the JSON is valid. Escape backslashes in strings (e.g., use \\\\n for newlines in code strings)."
        )
    else:
        # Fallback
        lens = "The General Improver Lens"
        prompt = (
             f"You are the 'Evolutionary Architect'. You are auditing the file `{filepath}`.\n"
             f"Propose any improvements found.\n\n"
             f"File Content:\n```python\n{content}\n```\n\n"
             f"File Content:\n```python\n{content}\n```\n\n"
             f"Output a JSON object with keys: 'summary', 'plan', 'diff'.\n"
             f"IMPORTANT: Ensure the JSON is valid. Escape backslashes in strings (e.g., use \\\\n for newlines in code strings)."
        )

    model = get_model_for_task("code_generation")
    
    max_retries = 3
    current_prompt = prompt
    
    for attempt in range(max_retries):
        try:
            response = await invoke_ollama_model_async(current_prompt, model_name=model, json_mode=True)

            if not response:
                logger.warning(f"EvolutionaryArchitect: No response received from LLM (Attempt {attempt+1}/{max_retries}). Aborting.")
                return None


            # Try to parse JSON from response
            proposal = _robust_json_parse(response)
            
            if not proposal:
                raise json.JSONDecodeError("Failed to parse using robust parser", response, 0)

            
            # If successful, return immediately
            return {
                "status": "proposal",
                "target_file": filepath,
                "metric": metric,
                "lens": lens,
                "proposal": proposal
            }

        except json.JSONDecodeError as e:
            logger.warning(f"EvolutionaryArchitect: Failed to parse LLM proposal (Attempt {attempt+1}/{max_retries}) for {filepath}: {e}")
            logger.debug(f"Row LLM Response was: {response}") # Added debug log
            # Add feedback to the prompt for the next attempt
            current_prompt = prompt + f"\n\nERROR: Your previous response was invalid JSON ({e}). Please fix the JSON formatting and try again. Ensure strings are properly escaped and the JSON is valid."
            
        except Exception as e:
            logger.warning(f"EvolutionaryArchitect: Unexpected error during proposal generation (Attempt {attempt+1}/{max_retries}): {e}")
            # For non-JSON errors, maybe just retry cleanly or stop? Let's retry.
            pass

    logger.error(f"EvolutionaryArchitect: Failed to generate valid proposal for {filepath} after {max_retries} attempts.")
    return None



async def perform_architectural_audit(effort_level: str = "normal") -> Optional[Dict[str, Any]]:
    """
    The main entry point for the Evolutionary Architect.
    Selects a file, analyzes it, and potentially generates a proposal.
    """
    # 1. Target Selection (Roulette Wheel)
    categories = list(TARGET_CATEGORIES.keys())
    weights = [TARGET_CATEGORIES[c]["weight"] for c in categories]

    selected_category_key = random.choices(categories, weights=weights, k=1)[0]
    category_config = TARGET_CATEGORIES[selected_category_key]

    paths = category_config.get("paths", [category_config.get("path")])

    candidate_files = []

    # Resolve project root
    # Assuming execution from project root
    project_root = os.getcwd()

    for path in paths:
        full_path = os.path.join(project_root, path)
        if not os.path.exists(full_path):
            continue

        for root, _, files in os.walk(full_path):
            for file in files:
                if file.endswith(".py"):
                    candidate_files.append(os.path.join(root, file))

    if not candidate_files:
        logger.info("EvolutionaryArchitect: No candidate files found.")
        return None

    # Select one file
    target_file = random.choice(candidate_files)

    import time
    from ai_assistant.config import get_data_dir
    heatmap_file = os.path.join(get_data_dir(), "architect_heatmap.json")

    # Load heatmap
    heatmap = {}
    if os.path.exists(heatmap_file):
        try:
            with open(heatmap_file, "r", encoding="utf-8") as f:
                heatmap = json.load(f)
        except json.JSONDecodeError:
            pass

    # Weight files based on heatmap (older audits have higher weight)
    now = time.time()
    file_weights = []
    for f in candidate_files:
        last_audited = heatmap.get(f, 0)
        # Weight = days since last audit + 1 (base weight)
        days_since = (now - last_audited) / 86400
        file_weights.append(max(1.0, days_since))

    # Select one file using heat map weights
    target_file = random.choices(candidate_files, weights=file_weights, k=1)[0]

    # Read file content
    try:
        with open(target_file, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        logger.error(f"EvolutionaryArchitect: Failed to read {target_file}: {e}")
        return None

    # 2. Phase 1: Static Analysis Filter
    analysis = StaticAnalysisFilter.analyze_file(target_file, content)

    if not analysis["accept"]:
        logger.info(f"EvolutionaryArchitect: File {target_file} rejected: {analysis['reason']}")
        # Update heatmap to note we checked it, so we don't spam simple files
        heatmap[target_file] = now
        with open(heatmap_file, "w", encoding="utf-8") as f:
            json.dump(heatmap, f)
        return None

    logger.info(f"EvolutionaryArchitect: File {target_file} ACCEPTED for audit. Reason: {analysis['reason']}")

    # 3. Phase 2: The Evolutionary Lenses
    proposal = await generate_evolution_proposal(target_file, content, analysis)

    # Update heatmap on success
    heatmap[target_file] = now
    with open(heatmap_file, "w", encoding="utf-8") as f:
        json.dump(heatmap, f)

    return proposal


def _robust_json_parse(json_str: str) -> Optional[Dict[str, Any]]:
    """
    Attempts to parse JSON with multiple fallback strategies, including regex extraction.
    """
    # 1. Try standard clean-up of markdown code blocks
    clean_str = json_str.strip()
    if clean_str.startswith("```json"):
        clean_str = clean_str[7:]
    elif clean_str.startswith("```"):
        clean_str = clean_str[3:]
    
    if clean_str.endswith("```"):
        clean_str = clean_str[:-3]
    
    clean_str = clean_str.strip()

    # 2. Try standard JSON parsing on cleaned string
    try:
        return json.loads(clean_str)
    except json.JSONDecodeError:
        pass

    # 3. Regex Extraction: Find the largest outer {} block
    # This handles cases where the LLM puts text before or after the JSON, 
    # or if the markdown stripping failed.
    try:
        # Find the first '{' and the last '}'
        start_idx = json_str.find('{')
        end_idx = json_str.rfind('}')
        
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            candidate = json_str[start_idx:end_idx+1]
            return json.loads(candidate)
    except (json.JSONDecodeError, Exception):
        pass

    # 4. Try ast.literal_eval (handles Python-specific syntax like single quotes)
    try:
        return ast.literal_eval(clean_str)
    except (ValueError, SyntaxError):
        pass

    # 5. Desperation move: replace newlines in strings
    try:
        cleaned_str = clean_str.replace('\n', '\\n') 
        return json.loads(cleaned_str)
    except json.JSONDecodeError:
        pass
        
    return None
