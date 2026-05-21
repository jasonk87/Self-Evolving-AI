import logging
from typing import Optional, Dict, Any
from ai_assistant.core.enums import ExecutionMode
from ai_assistant.config import DEFAULT_MODEL

logger = logging.getLogger(__name__)

ROUTER_PROMPT = """You are the AI Assistant's Execution Router. Your job is to classify the user's intent into one of three execution modes.

Modes:
1. DIRECT: Zero overhead. Simple input -> output. No tools, no complex logic.
   - Use for: Summarization, formatting, simple small talk, greetings, translation, simple Q&A without realtime data.
   - Examples: "Summarize this", "Fix typo", "Hello", "Translate this to Spanish".

2. FAST_REACT: Standard ReAct loop (Think -> Act -> Observe). Single-threaded, self-correcting.
   - Use for: Standard coding tasks, active chat, quick lookups, tool usage, debugging simple errors.
   - Examples: "Write a script", "Check weather", "Debug this error", "Find file X".

3. FAST_REACT: Tool-capable execution loop for complex requests.
   - Use for: Complex architecture, deep debugging, high-risk tasks, refactoring entire systems, planning large projects.
   - Examples: "Design a system", "Refactor entire backend", "Research and plan a new feature".

Instructions:
- Analyze the user's prompt and context.
- Output ONLY the mode name: DIRECT or FAST_REACT.
- Do not output any other text.
"""

import re

class TaskRouter:
    def __init__(self, llm_model: str = DEFAULT_MODEL):
        self.llm_model = llm_model
        
        # Pre-compile regex patterns for lightning-fast routing
        self.direct_patterns = [
            re.compile(r"^(hi|hello|hey|hola|ping|sup|greetings)[\!?\.]*$", re.IGNORECASE),
            re.compile(r"^(summarize|translate|format) ", re.IGNORECASE),
            re.compile(r"^(what is|define|who is) ", re.IGNORECASE)
        ]
        
        self.thinking_patterns = [
            re.compile(r"(architect|design|refactor entire|refactor all|deep analysis|comprehensive|brainstorm)", re.IGNORECASE),
            re.compile(r"plan (a|) (project|system|feature)", re.IGNORECASE),
            re.compile(r"create a (new |)project", re.IGNORECASE)
        ]
        
        self.fast_react_patterns = [
            re.compile(r"(write|create) a (script|function|class)", re.IGNORECASE),
            re.compile(r"(fix|debug|resolve) (this|error|bug)", re.IGNORECASE),
            re.compile(r"why is", re.IGNORECASE),
            re.compile(r"(test|run|check) ", re.IGNORECASE)
        ]

    async def determine_mode(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> ExecutionMode:
        """
        Determines the execution mode using a high-performance heuristic engine.
        Falls back to FAST_REACT by default to save LLM overhead.
        """
        prompt_clean = prompt.strip()
        
        # 1. Fast Heuristic Checks (Regex)
        for pattern in self.direct_patterns:
            if pattern.search(prompt_clean):
                logger.info("Router: Heuristic match -> DIRECT")
                return ExecutionMode.DIRECT
                
        for pattern in self.thinking_patterns:
            if pattern.search(prompt_clean):
                logger.info("Router: Heuristic match -> FAST_REACT")
                return ExecutionMode.FAST_REACT
                
        for pattern in self.fast_react_patterns:
            if pattern.search(prompt_clean):
                logger.info("Router: Heuristic match -> FAST_REACT")
                return ExecutionMode.FAST_REACT

        # 2. Dynamic Fallback
        # If no explicit pattern matches, FAST_REACT is the safest default for code/agent platforms.
        # This completely bypasses the LLM routing tax, saving 1-3 seconds per prompt.
        logger.info("Router: No explicit heuristic match -> Defaulting to FAST_REACT")
        return ExecutionMode.FAST_REACT
