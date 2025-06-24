# ai_assistant/planning/llm_argument_parser.py
from typing import Tuple, List, Dict, Any, Optional
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model
from ai_assistant.config import get_model_for_task # Added import
import json
import re

LLM_ARG_POPULATION_PROMPT_TEMPLATE = """Given the user's overall goal: "{goal_description}"
And the specific tool selected:
  Tool Name: "{tool_name}"
  Tool Description: "{tool_description}" (This description may include details about expected arguments like names, types, and if they are positional or keyword-based.)

Your task is to identify and extract the arguments for the tool "{tool_name}" from the user's goal.
Respond ONLY with a JSON object containing two keys:
- "args": A list of strings, representing the positional arguments in the correct order.
- "kwargs": A dictionary where keys are argument names (strings) and values are the corresponding argument values (strings).

If no suitable value for an argument is found in the goal, represent it as an empty string "" or omit it if appropriate for keyword arguments.
If the tool description specifies argument names (e.g., "requires 'filename' and 'content'"), use those as keys in "kwargs" if they seem like keyword arguments.
If the tool description implies positional arguments (e.g., "takes two numbers"), fill the "args" list.

Example Response (tool expects two positional args and one kwarg):
{{"args": ["10", "blue"], "kwargs": {{"output_file": "results.txt"}}}}
Another Example (no kwargs):
{{"args": ["some_value"], "kwargs": {{}}}}
Another Example (no args):
{{"args": [], "kwargs": {{"param1": "value1"}}}}
If an argument is mentioned in the description but not found in the goal for the tool, you can represent it as an empty string in "args" or omit from "kwargs".

JSON object:
"""

def populate_tool_arguments_with_llm(
    goal_description: str,
    tool_name: str,
    tool_description: str,
    ollama_model_name: Optional[str] = None 
) -> Tuple[List[str], Dict[str, str]]:
    """
    Uses an LLM to populate arguments for a given tool based on a goal description.
    """
    model_to_use = ollama_model_name if ollama_model_name is not None else get_model_for_task("argument_population")
    formatted_prompt = LLM_ARG_POPULATION_PROMPT_TEMPLATE.format(
        goal_description=goal_description,
        tool_name=tool_name,
        tool_description=tool_description
    )
    
    print(f"\nLLMArgParser: Sending prompt to populate args for '{tool_name}' using model '{model_to_use}' (Goal: '{goal_description[:50]}...'):\nPrompt (first 300 chars): {formatted_prompt[:300]}...")

    llm_response_str = invoke_ollama_model(formatted_prompt, model_name=model_to_use)

    if not llm_response_str:
        print(f"LLMArgParser: Received no response from LLM ({model_to_use}) for argument population.")
        return ([], {})

    print(f"LLMArgParser: Raw response from LLM for args:\n---\n{llm_response_str}\n---")

    json_str_to_parse = llm_response_str
    # Sanitize: remove markdown and potential "JSON object:" prefix
    match = re.search(r"```json\s*([\s\S]*?)\s*```", json_str_to_parse)
    if match:
        json_str_to_parse = match.group(1)
    json_str_to_parse = re.sub(r"^\s*JSON object:?\s*", "", json_str_to_parse.strip(), flags=re.IGNORECASE).strip()

    try:
        parsed_json = json.loads(json_str_to_parse)
    except json.JSONDecodeError as e:
        print(f"LLMArgParser: Failed to parse JSON response for arguments. Error: {e}")
        print(f"LLMArgParser: Attempted to parse: '{json_str_to_parse}'")
        return ([], {})

    if not isinstance(parsed_json, dict):
        print(f"LLMArgParser: Parsed JSON is not a dictionary. Got: {type(parsed_json)}")
        return ([], {})

    raw_args = parsed_json.get("args")
    raw_kwargs = parsed_json.get("kwargs")

    # Validate and sanitize args
    final_args: List[str] = []
    if isinstance(raw_args, list):
        final_args = [str(arg) for arg in raw_args]
    elif raw_args is not None: # If it's present but not a list
        print(f"LLMArgParser: Warning - 'args' from LLM was not a list (got {type(raw_args)}). Using empty list.")
    
    # Validate and sanitize kwargs
    final_kwargs: Dict[str, str] = {}
    if isinstance(raw_kwargs, dict):
        final_kwargs = {str(k): str(v) for k, v in raw_kwargs.items()}
    elif raw_kwargs is not None: # If it's present but not a dict
        print(f"LLMArgParser: Warning - 'kwargs' from LLM was not a dictionary (got {type(raw_kwargs)}). Using empty dict.")

    print(f"LLMArgParser: Successfully parsed args: {final_args}, kwargs: {final_kwargs} for tool '{tool_name}'")
    return (final_args, final_kwargs)


if __name__ == '__main__':
    print("--- Testing LLM Argument Parser ---")
    
    # Mock invoke_ollama_model for testing this module directly
    # Store original function to restore later
    original_invoke_ollama = invoke_ollama_model
    
    def mock_invoke_ollama(prompt: str, model_name: str, **kwargs) -> Optional[str]:
        print(f"\n--- MOCK OLLAMA CALL ---")
        print(f"Model: {model_name}")
        print(f"Prompt (first 150 chars for test): {prompt[:150]}...")
        
        # Simulate different LLM responses based on prompt content for testing
        if "add 75 and 20" in prompt and "add_numbers" in prompt:
            return """```json
            {
                "args": ["75", "20"],
                "kwargs": {}
            }
            ```"""
        elif "greet User" in prompt and "greet_user" in prompt:
             return """JSON object:
             {
                 "args": ["User"],
                 "kwargs": {"title": "Esteemed"}
             }"""
        elif "subtract 10 from 30" in prompt and "subtract_tool" in prompt:
             return """{
                 "args": ["30", "10"],
                 "kwargs": {}
             }"""
        elif "no_real_args_here" in prompt and "test_tool_no_args" in prompt:
             return """{
                 "args": [],
                 "kwargs": {}
             }"""
        elif "bad_json_response" in prompt:
            return "This is not JSON { definitely not"
        elif "not_dict_response" in prompt:
            return "[\"just_a_list\"]" # Valid JSON, but not a dict
        elif "bad_args_type" in prompt:
            return """{
                "args": "not_a_list", 
                "kwargs": {"key": "value"}
            }"""
        elif "bad_kwargs_type" in prompt:
            return """{
                "args": ["valid_arg"], 
                "kwargs": "not_a_dict"
            }"""
        return None # Default to no response

    # Replace the actual function with the mock
    from ai_assistant.llm_interface import ollama_client
    ollama_client.invoke_ollama_model = mock_invoke_ollama


    # Test cases
    print("\n--- Test Case 1: Add numbers ---")
    args, kwargs = populate_tool_arguments_with_llm(
        goal_description="Can you add 75 and 20 for me?",
        tool_name="add_numbers",
        tool_description="Adds two numbers a and b."
    )
    print(f"Result: args={args}, kwargs={kwargs}")
    assert args == ["75", "20"]
    assert kwargs == {}

    print("\n--- Test Case 2: Greet user with kwargs ---")
    args, kwargs = populate_tool_arguments_with_llm(
        goal_description="Please greet User",
        tool_name="greet_user",
        tool_description="Greets a person. Takes name as positional arg, and optional 'title' as kwarg."
    )
    print(f"Result: args={args}, kwargs={kwargs}")
    assert args == ["User"]
    assert kwargs == {"title": "Esteemed"}
    
    print("\n--- Test Case 3: Tool with no real args in goal ---")
    args, kwargs = populate_tool_arguments_with_llm(
        goal_description="no_real_args_here",
        tool_name="test_tool_no_args",
        tool_description="A test tool that takes no specific args from this goal."
    )
    print(f"Result: args={args}, kwargs={kwargs}")
    assert args == []
    assert kwargs == {}

    print("\n--- Test Case 4: Bad JSON response ---")
    args, kwargs = populate_tool_arguments_with_llm(
        goal_description="bad_json_response", 
        tool_name="test_bad_json", 
        tool_description="Tool that will get bad JSON."
    )
    print(f"Result: args={args}, kwargs={kwargs}")
    assert args == []
    assert kwargs == {}
    
    print("\n--- Test Case 5: LLM returns JSON list instead of dict ---")
    args, kwargs = populate_tool_arguments_with_llm(
        goal_description="not_dict_response", 
        tool_name="test_not_dict", 
        tool_description="Tool that will get a JSON list."
    )
    print(f"Result: args={args}, kwargs={kwargs}")
    assert args == []
    assert kwargs == {}

    print("\n--- Test Case 6: LLM returns args not as list ---")
    args, kwargs = populate_tool_arguments_with_llm(
        goal_description="bad_args_type", 
        tool_name="test_bad_args_type", 
        tool_description="Tool that will get args not as list."
    )
    print(f"Result: args={args}, kwargs={kwargs}")
    assert args == [] # Should default to empty list
    assert kwargs == {"key": "value"}


    print("\n--- Test Case 7: LLM returns kwargs not as dict ---")
    args, kwargs = populate_tool_arguments_with_llm(
        goal_description="bad_kwargs_type", 
        tool_name="test_bad_kwargs_type", 
        tool_description="Tool that will get kwargs not as dict."
    )
    print(f"Result: args={args}, kwargs={kwargs}")
    assert args == ["valid_arg"]
    assert kwargs == {} # Should default to empty dict
    
    print("\n--- Test Case 8: No response from LLM ---")
    # For this, the mock needs to return None. Our mock returns None by default if no conditions met.
    args, kwargs = populate_tool_arguments_with_llm(
        goal_description="This goal won't match any mock conditions",
        tool_name="any_tool",
        tool_description="Any description"
    )
    print(f"Result: args={args}, kwargs={kwargs}")
    assert args == []
    assert kwargs == {}

    # Restore original function
    ollama_client.invoke_ollama_model = original_invoke_ollama
    print("\n--- LLM Argument Parser Tests Finished (mocked Ollama) ---")
