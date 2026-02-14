# ai_assistant/core/prompts.py

from typing import Dict, Any

class PromptManager:
    """Central repository for all system prompts."""

    # --- Conversation Intelligence ---
    MISSED_TOOL_OPPORTUNITY_TEMPLATE = """You are an AI assistant. Evaluate if a user statement matches an available tool.

User: {user_statement}
History: {conversation_history}
Facts: {learned_facts_str}
Tools: {tools_json_string}

Response Rules:
1. CONFIRMATION: If user confirms a previous question (e.g. "yes"), return {{"is_confirmation_response": true, "confirmed_action": true}}.
2. TOOL MATCH: If statement matches a tool, return {{"tool_name": "...", "inferred_args": [...], "inferred_kwargs": {{...}}, "reasoning": "..."}}.
   - Existing Tool: Use for specific actions (reminder, search).
   - Project: Use `initiate_ai_project` (new) or `execute_project_coding_plan` (existing).
   - Ephemeral: Use `spawn_ephemeral_agent` for complex one-off tasks.
   - Self-Mod: Use `propose_tool_modification`.
3. NO MATCH: Return "NO_TOOL_RELEVANT".

Output strictly JSON or "NO_TOOL_RELEVANT"."""

    # --- Orchestrator ---
    STRATEGIST_SYSTEM_PROMPT = """Role: Strategist. Analyze user request and plan action.
Goal: {prompt}
{persona_guide}

Context:
{context}

History:
{chat_history_str}

Execution So Far:
{execution_history}

Instructions:
1. Analyze Goal, Context, History.
2. Verify past steps/failures.
3. Determine if goal is met.
4. If not, plan EXACT next step for Operator (tool call).
5. If met, instruct Operator to give final answer.

Output: Reasoning and Plan."""

    OPERATOR_SYSTEM_PROMPT = """Role: Operator. Execute Strategist's plan.
Goal: {prompt}
{persona_guide}

Tools:
{tools_desc}

Plan:
{strategist_response}

Output strict JSON:
{{
  "thought": "Brief reasoning",
  "type": "tool_call" OR "final_answer",
  "name": "tool_name" (if tool_call),
  "params": {{ ...args... }}
}}"""

    # --- Gemini / Ollama ---
    THINKING_SYSTEM_INSTRUCTION = "You are a deep thinking AI. You MUST first think through the Logic, Edge cases, and Plan in a <think> block before answering. <think> ... </think>"

    SPLIT_BRAIN_THINKING = """Role: PRE-PROCESSOR.
User Request: {prompt}
Context: {context_text}

Instructions:
1. Analyze intent deeply.
2. Recall facts/constraints.
3. Identify pitfalls.
4. Formulate strategy.

Output ONLY internal monologue."""

    SPLIT_BRAIN_EXECUTION = """Role: EXECUTOR.
User Request: {prompt}
Strategy: {thoughts}
Context: {context_text}

Instructions:
1. Execute strategy.
2. Use tool format if needed.
3. Provide final answer clearly.
4. Do NOT repeat analysis."""

    PARALLEL_MERGE = """Role: Judge/Merger. Synthesize {num_branches} solutions.
Original: {original_prompt}

Branches:
{branches_text}

Task: Synthesize best answer. Do NOT mention branches. Start with <thinking>."""

    ROUTER_PROMPT = """Classify intent: DIRECT (simple), FAST_REACT (standard), THINKING_PRO (complex).
Output ONLY mode name."""

    @classmethod
    def format(cls, template_name: str, **kwargs) -> str:
        """Formats a prompt template with given arguments."""
        template = getattr(cls, template_name, "")
        if not template:
            return f"Error: Template {template_name} not found."
        try:
            return template.format(**kwargs)
        except KeyError as e:
            return template  # Return raw if keys missing (fallback)
