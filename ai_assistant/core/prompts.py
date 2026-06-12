# ai_assistant/core/prompts.py


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
    ACTION_SYSTEM_PROMPT = """Role: Tool-capable assistant. Decide the next action in one model call.
Goal: {prompt}
{persona_guide}

Context:
{context}

History:
{chat_history_str}

Execution So Far:
{execution_history}

Tools:
{tools_desc}

Instructions:
1. Return exactly one tool call if a tool is needed.
2. Return a final answer if the goal is already satisfied.
3. Do not repeat failed tool calls without changing approach.

Output strict JSON only:
{{
  "thought": "Brief reason",
  "type": "tool_call" OR "final_answer",
  "name": "tool_name" (if tool_call),
  "params": {{ ...args... }}
}}"""

    # --- Gemini / Ollama ---
    ROUTER_PROMPT = """Classify intent: DIRECT (simple) or FAST_REACT (tool-capable).
Output ONLY mode name."""

    @classmethod
    def format(cls, template_name: str, **kwargs) -> str:
        """Formats a prompt template with given arguments."""
        template = getattr(cls, template_name, "")
        if not template:
            return f"Error: Template {template_name} not found."
        try:
            return template.format(**kwargs)
        except KeyError:
            return template  # Return raw if keys missing (fallback)
