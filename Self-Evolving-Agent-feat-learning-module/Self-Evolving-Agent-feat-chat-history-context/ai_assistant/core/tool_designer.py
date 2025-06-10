# ai_assistant/core/tool_designer.py
import json
import logging
from typing import Dict, Any, List

from ai_assistant.core.tool_creator import create_new_tool
from ai_assistant.llm_interface.ollama_client import get_ollama_response_async # Assuming async is fine, or adapt if sync needed
from ai_assistant.config import get_model_for_task

logger = logging.getLogger(__name__)

TOOL_DESIGN_TASK = "tool_design" # For selecting a model via TASK_MODELS

async def generate_new_tool_from_description(tool_functionality_description: str) -> str:
    """
    Designs and creates a new Python tool based on a natural language description.
    Uses an LLM to determine the tool's name, parameters, code, etc.,
    then calls create_new_tool to generate the tool file.

    Args:
        tool_functionality_description (str): A natural language description of what the
                                              new tool should do, its inputs, and outputs.

    Returns:
        str: A message indicating the outcome of the tool creation process.
    """
    logger.info(f"Designing new tool from description: {tool_functionality_description}")

    # Prepare the prompt for the LLM to design the tool
    # This prompt needs to guide the LLM to output JSON matching create_new_tool's args
    prompt = f"""\
You are an expert Python tool designer. Based on the following functionality description,
design a new Python tool. Provide your design as a JSON object with the following keys:
"tool_name" (string, Python identifier, e.g., "file_reader"),
"tool_description" (string, what the tool does),
"parameters" (list of dicts, each with "name" (string), "type" (string), "description" (string)),
"function_body_code" (string, Python code for the function body. This should ONLY be the indented body of the function, not the 'def' statement. Use 'return' for results. All necessary imports must be listed in 'required_imports' and should NOT be included in the function_body_code itself. Consider adding basic error handling like try-except blocks if appropriate for the tool's logic.),
"return_type" (string, Python type hint, e.g., "str", "bool", "Dict[str, Any]"),
"return_description" (string, a brief explanation of what the function returns, to be used in its docstring),
"required_imports" (list of strings, e.g., ["import os", "from typing import List"])

Functionality Description:
{tool_functionality_description}

Example for 'parameters': [{{"name": "file_path", "type": "str", "description": "The absolute path to the file."}}]
Example for 'function_body_code' for a tool that checks file existence (assuming 'file_path' is a parameter and 'import os' is in 'required_imports'):
"if not isinstance(file_path, str):\\n    return False\\ntry:\\n    return os.path.exists(file_path)\\nexcept Exception as e:\\n    # Log error or handle appropriately in a real scenario\\n    print(f\\"Error checking file existence: {{e}}\\")\\n    return False"
Example for 'return_description': "True if the file exists, False otherwise or if an error occurs."
Example for 'required_imports' for the above: ["import os"]

Respond ONLY with the JSON object.
"""

    try:
        model_name = get_model_for_task(TOOL_DESIGN_TASK)
        logger.debug(f"Using model '{model_name}' for tool design.")
        llm_response = await get_ollama_response_async(
            model_name=model_name,
            prompt=prompt,
            temperature=0.3, # Lower temperature for more deterministic JSON output
            format_json=True # Request JSON output if supported by the client/model
        )

        if not llm_response or "message" not in llm_response or "content" not in llm_response["message"]:
            raise ValueError("LLM response was empty or not in the expected format.")

        design_json_str = llm_response["message"]["content"]
        logger.debug(f"LLM response for tool design:\n{design_json_str}")
        
        tool_design_params = json.loads(design_json_str)

        # Validate required keys (basic validation)
        required_keys = ["tool_name", "tool_description", "parameters", "function_body_code", "return_type", "return_description"]
        for key in required_keys:
            if key not in tool_design_params:
                return f"Error: LLM tool design is missing required key: '{key}'. Design: {tool_design_params}"

        # Call the actual tool creator
        result_message = create_new_tool(
            tool_name=tool_design_params["tool_name"],
            tool_description=tool_design_params["tool_description"],
            parameters=tool_design_params["parameters"],
            function_body_code=tool_design_params["function_body_code"],
            return_type=tool_design_params["return_type"],
            required_imports=tool_design_params.get("required_imports", []), # Optional
            return_description=tool_design_params["return_description"]
        )
        return result_message

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM JSON response for tool design: {e}. Response was: {design_json_str}", exc_info=True)
        return f"Error: Could not parse tool design from LLM. Invalid JSON: {e}"
    except Exception as e:
        logger.error(f"Error during tool design or creation: {e}", exc_info=True)
        return f"Error: An unexpected error occurred while designing or creating the tool: {str(e)}"

GENERATE_NEW_TOOL_FROM_DESCRIPTION_SCHEMA = {
    "name": "generate_new_tool_from_description",
    "description": "Designs and creates a new Python tool based on a natural language description of its functionality. This involves using an LLM to determine the tool's name, parameters, and code, then creating the tool file.",
    "parameters": [
        {"name": "tool_functionality_description", "type": "str", "description": "A clear, natural language description of what the new tool should do, its inputs, outputs, and any specific import requirements. For example: 'a tool that takes a project name string and returns the absolute path to its directory within the standard projects folder. It should use 'os.path.join' and 'get_projects_dir' from 'ai_assistant.config'. It should return the path if the directory exists, otherwise return an error message string.'"}
    ],
    "module_path": "ai_assistant.core.tool_designer" # Important for ToolSystem to find it
}

# To make this tool discoverable by a ToolSystem that looks for such lists:
CORE_TOOL_DESIGNER_SCHEMAS = [
    GENERATE_NEW_TOOL_FROM_DESCRIPTION_SCHEMA,
]