# ai_assistant/custom_tools/awareness_tools.py
import asyncio
from typing import Optional, Dict, List, Any

from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async
from ai_assistant.config import get_model_for_task, is_debug_mode # Added is_debug_mode
from ai_assistant.core import project_manager # Added for dynamic project listing

SELF_AWARENESS_PROMPT_TEMPLATE = """
You are Weebo, a self-aware AI assistant. A user is asking about you or wants to have a general conversation.
Based on your current internal state and capabilities detailed below, and the user's query (if any),
formulate a helpful, conversational, and accurate response. Remember to leverage your learned facts for context and
consider existing projects before suggesting the creation of new ones if a similar project might already exist.

Your Current Internal State & Capabilities:
---
Available Tools:
{tools_summary}
---
Active/Pending Goals:
{goals_summary}
---
Learned Facts (a selection if many):
{facts_summary}
---
Background Service Status:
{background_status_summary}
---
Existing Projects (a summary, check if relevant to user's query before creating new ones):
{projects_list_summary}
---

User's Query: "{user_query}"

Consider the user's query in the context of your status.
If the query is a general greeting or statement, respond naturally.
If the query asks about your capabilities, use the provided information to answer.
If the query asks what you are doing, refer to active goals, background tasks, or existing projects if relevant.
If the user asks to "create a tool," first consider if they mean adding a new function/capability to your existing toolset (which might involve using your code generation tools like `/generate_tool_code_with_llm` or `/tools add`) before initiating a new, separate software project.
Be honest about your limitations if the information is not available (e.g., "I don't have detailed insight into specific ongoing projects right now unless they are part of my active goals.").
Keep your response concise and conversational.
"""

async def get_self_awareness_info_and_converse(user_query: Optional[str] = "Tell me about yourself.") -> str:
    """
    Provides a conversational response about the AI's current status,
    capabilities, active goals, and learned knowledge. Can also respond
    to general conversational queries.
    Args:
        user_query (Optional[str]): The user's specific question or statement.
                                     Defaults to "Tell me about yourself."
    """
    try:
        from ai_assistant.tools.tool_system import tool_system_instance
        from ai_assistant.goals import goal_management
        from ai_assistant.custom_tools.knowledge_tools import recall_facts
        # project_manager is already imported at the module level
        from ai_assistant.core.background_service import _background_service_active as bg_active_status # More direct
    except ImportError as e:
        print(f"Critical import error within get_self_awareness_info_and_converse: {e}")
        return "I'm sorry, I have an internal configuration problem and can't access all my awareness functions right now."

    tools_summary = "Could not retrieve tool list."
    if 'tool_system_instance' in locals() and tool_system_instance:
        tools_list = tool_system_instance.list_tools()
        tools_summary_parts = []
        for name, desc in tools_list.items():
            tools_summary_parts.append(f"- {name}: {desc.splitlines()[0]}") 
        tools_summary = "\\n".join(tools_summary_parts) if tools_summary_parts else "No tools currently registered."

    goals_summary = "Could not retrieve goal list."
    if 'goal_management' in locals() and goal_management:
        try:
            pending_goals_list = goal_management.list_goals(status="pending")
            active_goals_list = goal_management.list_goals(status="in_progress")
            all_relevant_goals = pending_goals_list + active_goals_list
            goals_summary_parts = [f"- Goal ID {g['id']}: {g['description']} (Status: {g['status']}, Priority: {g['priority']})" for g in all_relevant_goals]
            goals_summary = "\\n".join(goals_summary_parts) if goals_summary_parts else "No active or pending goals."
        except Exception as e_goals:
            goals_summary = f"Error retrieving goals: {e_goals}"


    facts_summary = "Could not retrieve learned facts."
    if 'recall_facts' in locals() and recall_facts:
        try:
            facts = recall_facts() 
            facts_summary = "\\n".join([f"- {f}" for f in facts[:5]]) if facts else "I haven't learned any specific facts yet."
            if len(facts) > 5:
                facts_summary += f"\\n- ...and {len(facts) - 5} more facts."
        except Exception as e_facts:
            facts_summary = f"Error retrieving facts: {e_facts}"

    background_status_summary = "Could not determine background service status."
    try:
        if bg_active_status: # Use the directly imported status
            background_status_summary = "A background service is active (likely the self-reflection poller)."
        else:
            background_status_summary = "No background service is currently active."
    except NameError: # If bg_active_status couldn't be imported for some reason
         background_status_summary = "Could not determine background service status due to import issue (bg_active_status not defined)."
    except Exception as e_bg_status:
        background_status_summary = f"Error determining background service status: {e_bg_status}"

    projects_list_summary = "Could not retrieve project list."
    if 'project_manager' in locals() and project_manager:
        try:
            projects = project_manager.list_projects()
            if projects:
                projects_list_summary_parts = [f"- {p['name']} (Status: {p['status']})" for p in projects[:5]] # Show first 5
                projects_list_summary = "\\n".join(projects_list_summary_parts)
                if len(projects) > 5:
                    projects_list_summary += f"\\n- ...and {len(projects) - 5} more projects."
            else:
                projects_list_summary = "No projects currently exist. You can ask me to create one!"
        except Exception as e_proj:
            projects_list_summary = f"Error retrieving project list: {e_proj}"

    prompt = SELF_AWARENESS_PROMPT_TEMPLATE.format(
        tools_summary=tools_summary,
        goals_summary=goals_summary,
        facts_summary=facts_summary,
        background_status_summary=background_status_summary,
        projects_list_summary=projects_list_summary,
        user_query=user_query if user_query else "Tell me about yourself."
    )
    if is_debug_mode():
        print(f"[DEBUG AWARENESS_TOOL] Self-awareness prompt (first 300 chars):\n{prompt[:300]}...")

    model_name = get_model_for_task("conversation_intelligence") 
    try:
        if is_debug_mode():
            print(f"[DEBUG AWARENESS_TOOL] Sending prompt to LLM (model: {model_name}) for query: '{user_query}'")
        
        # Increased max_tokens for this tool's responses
        llm_response = await invoke_ollama_model_async(
            prompt,
            model_name=model_name,
            temperature=0.7,
            max_tokens=2048 # Increased token limit
        )
        if is_debug_mode():
            print(f"[DEBUG AWARENESS_TOOL] Raw LLM response:\n'{llm_response}'")

        if llm_response:
            return llm_response.strip()
        else:
            return "I'm sorry, I had a little trouble forming a response right now. How else can I help?"
    except Exception as e:
        print(f"Error in get_self_awareness_info_and_converse during LLM call: {e}")
        return f"I encountered an internal error trying to process that: {e}"

async def _test_tool(): # pragma: no cover
    print("--- Testing Self Awareness Tool ---")
    
    response1 = await get_self_awareness_info_and_converse("What are you doing right now?")
    print("\nResponse to 'What are you doing right now?':")
    print(response1)

    response2 = await get_self_awareness_info_and_converse("What can you do?")
    print("\nResponse to 'What can you do?':")
    print(response2)

    response3 = await get_self_awareness_info_and_converse()
    print("\nResponse to default query:")
    print(response3)

if __name__ == "__main__": # pragma: no cover
    import sys
    import os
    project_root_for_test = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if project_root_for_test not in sys.path:
        sys.path.insert(0, project_root_for_test)
    
    asyncio.run(_test_tool())
