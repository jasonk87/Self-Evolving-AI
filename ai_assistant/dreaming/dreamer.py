import os
import logging
import asyncio
from typing import List, Dict, Any, Optional
import json

from ai_assistant.llm_interface.gemini_client import invoke_gemini_model_async
from ai_assistant.tools import tool_system 
from ai_assistant.core import self_modification

logger = logging.getLogger(__name__)

class DreamerAgent:
    """
    The DreamerAgent is responsible for 'hallucinating' hypothetical user scenarios 
    and edge cases during system idle time. It generates test scripts to verify 
    hypothesis about code robustness.
    """

    def __init__(self):
        self.system_prompt = """
You are the "Dreamer" module of an advanced AI. 
Your goal is to test the robustness of the system by imagining "What If" scenarios.
You look at source code and ask:
- "What if the input is empty?"
- "What if the network fails?"
- "What if the file is 10GB?"
- "What if the user passes a string instead of an int?"

You Generate:
1. A Description of the hypothetical scenario.
2. A stand-alone Python script that attempts to reproduce this scenario against the imported tool.
"""

    async def propose_dream_scenario(self, target_tool_name: str, target_code: str) -> Dict[str, Any]:
        """
        Analyzes a tool's source code and proposes a difficult "What If" scenario.
        """
        prompt = f"""
{self.system_prompt}

TARGET TOOL: `{target_tool_name}`

SOURCE CODE:
```python
{target_code}
```

Task:
1. Identify a potential edge case or weakness in the code (lack of validation, error handling, etc).
2. Create a "Dream Scenario" to test this.
3. Write a COMPLETE, STANDALONE Python script to verify it.
   - The script must import the tool (assume it's importable from its module path).
   - Ensure all standard libraries used (e.g., sys, os, time) are explicitly imported.
   - If the crash happens, the script should print "DREAM_CRASH_DETECTED: <reason>" and exit with code 1.

   - If it handles it handles it gracefully (e.g. raises proper error or returns), it should print "DREAM_SURVIVED".

Output JSON format:
{{
    "scenario_name": "Short name",
    "description": "What are testing?",
    "verification_script": "Full python code..."
}}
"""
        response = await invoke_gemini_model_async(prompt, temperature=0.7) # High temp for creativity
        
        try:
            # Clean generic markdown
            clean_res = response.strip()
            if "```json" in clean_res:
                clean_res = clean_res.split("```json")[1].split("```")[0]
            elif "```" in clean_res:
                 clean_res = clean_res.split("```")[1]
            
            data = json.loads(clean_res)
            return data
        except Exception as e:
            logger.error(f"DreamerAgent: Failed to parse dream response: {e}")
            return None

    async def realize_dream(self, tool_name: str) -> Dict[str, Any]:
        """
        Executes a full dream cycle for a specific tool:
        1. Read code.
        2. Hallucinate scenario.
        3. (The actual running part would be handled by a sandbox executor, 
            but here we return the plan for the Background Service to execute)
        """
        tool_info = tool_system.tool_system_instance.get_tool(tool_name)
        if not tool_info:
            return {"error": f"Tool {tool_name} not found"}

        module_path = tool_info.get("module_path")
        func_name = tool_info.get("function_name")
        
        code = self_modification.get_function_source_code(module_path, func_name)
        if not code:
            return {"error": "Could not read source code"}

        scenario = await self.propose_dream_scenario(tool_name, code)
        if not scenario:
            return {"error": "Dream generation failed"}

        # Augment with metadata
        scenario["target_tool"] = tool_name
        scenario["target_module"] = module_path
        
        return scenario
