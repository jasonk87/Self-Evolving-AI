
import os
import ast
import random
import json
import logging
import asyncio
from typing import Optional, Dict, List, Any
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async
from ai_assistant.config import get_model_for_task, is_debug_mode

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

        # Comment check
        has_todo = "TODO" in content or "FIXME" in content or "HACK" in content

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
            f"Output a JSON object with keys: 'summary', 'plan', 'diff' (optional, concept diff)."
        )
    elif metric == "deprecated":
        lens = "The Modernizer Lens"
        prompt = (
            f"You are the 'Evolutionary Architect'. You are auditing the file `{filepath}`.\n"
            f"The static analysis detected POTENTIALLY DEPRECATED or UNSAFE PATTERNS (e.g., os.system, shell=True).\n"
            f"Your goal: Propose a modern, safer alternative using `subprocess`, `shlex`, or other modern libraries.\n\n"
            f"File Content:\n```python\n{content}\n```\n\n"
            f"Output a JSON object with keys: 'summary', 'plan', 'diff' (optional, concept diff)."
        )
    elif metric == "todo":
        lens = "The Completion Lens"
        prompt = (
            f"You are the 'Evolutionary Architect'. You are auditing the file `{filepath}`.\n"
            f"The static analysis detected TODOs, FIXMEs, or HACKs.\n"
            f"Your goal: Propose code to implement the missing functionality or clean up the hack.\n\n"
            f"File Content:\n```python\n{content}\n```\n\n"
            f"Output a JSON object with keys: 'summary', 'plan', 'diff' (optional, concept diff)."
        )
    else:
        # Fallback
        lens = "The General Improver Lens"
        prompt = (
             f"You are the 'Evolutionary Architect'. You are auditing the file `{filepath}`.\n"
             f"Propose any improvements found.\n\n"
             f"File Content:\n```python\n{content}\n```\n\n"
             f"Output a JSON object with keys: 'summary', 'plan', 'diff'."
        )

    model = get_model_for_task("code_generation")
    response = await invoke_ollama_model_async(prompt, model_name=model)

    # Try to parse JSON from response
    try:
        if "```json" in response:
            json_str = response.split("```json")[1].split("```")[0]
        elif "```" in response:
            json_str = response.split("```")[1].split("```")[0]
        else:
            json_str = response

        proposal = json.loads(json_str)
    except Exception as e:
        logger.warning(f"Failed to parse LLM proposal for {filepath}: {e}")
        return None

    return {
        "status": "proposal",
        "target_file": filepath,
        "metric": metric,
        "lens": lens,
        "proposal": proposal
    }

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
        return None

    logger.info(f"EvolutionaryArchitect: File {target_file} ACCEPTED for audit. Reason: {analysis['reason']}")

    # 3. Phase 2: The Evolutionary Lenses
    proposal = await generate_evolution_proposal(target_file, content, analysis)

    return proposal
