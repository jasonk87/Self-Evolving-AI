# ai_assistant/custom_tools/my_extra_tools.py
from duckduckgo_search import DDGS
import json
from typing import Optional, Union, List, Dict, Any, Tuple # Added List, Dict, Any, Tuple
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model
from ai_assistant.config import get_model_for_task, GOOGLE_API_KEY as CFG_GOOGLE_API_KEY, GOOGLE_CSE_ID as CFG_GOOGLE_CSE_ID
import os # For environment variables

def subtract_numbers(a: float, b: float) -> Union[float, str]:
    """Subtracts the second number from the first."""
    # The tool system passes arguments as strings if coming directly from CLI's /execute_tool
    # For dynamic registration, the function signature is key.
    # If the planner uses this, it should provide correct types.
    # If CLI's /execute_tool is used, it passes strings.
    try:
        return float(a) - float(b)
    except ValueError:
        return "Error: Invalid input. 'a' and 'b' must be numbers for subtract_numbers."

def echo_message(message: str, repeat: int = 1) -> str:
    """Repeats a message a specified number of times."""
    try:
        num_repeats = int(repeat)
        if num_repeats < 0:
            return "Error: repeat count cannot be negative."
    except ValueError:
        return "Error: repeat count must be an integer."
    return ' '.join([str(message)] * num_repeats)

def search_duckduckgo(*args, **kwargs) -> str:
    """
    Searches the internet using DuckDuckGo and returns the results as a JSON string.

    Args:
        *args: Positional arguments. The first one is taken as the query.
        **kwargs: Keyword arguments. 'query' is checked here.

    Returns:
        str: A JSON string representing a list of search results. Each result in the
             list is a dictionary containing 'title', 'href' (URL), and 'body' (snippet)
             keys. Returns an empty JSON list '[]' if an error occurs during the search,
             if no results are found, or if the results are not in the expected format.
             Typically returns up to the top 5 results.
    """
    query: Optional[str] = None
    if 'query' in kwargs:
        query = str(kwargs['query'])
    elif args:
        query = str(args[0]) # Take the first positional argument as the query
    
    if not query:
        print("Error: search_duckduckgo requires a query argument.")
        return "[]" # Return empty JSON list string as per original behavior on error
    
    results = []
    try:
        with DDGS() as ddgs:
            search_results = ddgs.text(query, max_results=5)
        
        if search_results:
            for r in search_results:
                if isinstance(r, dict) and 'title' in r and 'href' in r and 'body' in r:
                    results.append({
                        "title": r['title'],
                        "href": r['href'],
                        "body": r['body']
                    })
                else:
                    print(f"Warning: search_duckduckgo received an unexpected result format: {r}")
            
    except Exception as e:
        print(f"Error during DuckDuckGo search for query '{query}': {e}")
    
    try:
        return json.dumps(results)
    except TypeError as te:
        print(f"Error serializing search results to JSON for query '{query}': {te}. Results: {results}")
        return "[]"

def search_google_custom_search(query: str, num_results: Union[int, str] = 5) -> str:
    """
    Searches Google. Args: query (str). Optional in kwargs: num_results (str, 1-10, default '5').

    Args:
        query (str): The search term.
        num_results (Union[int, str]): Number of results to return (max 10 for Google CSE).
                                       Planner will pass this as a string in kwargs.

    Returns:
        str: A JSON string representing a list of search results, each with
             'title', 'href' (URL), and 'body' (snippet). Returns '[]' on error.
    """
    api_key = os.environ.get("GOOGLE_API_KEY") or CFG_GOOGLE_API_KEY
    cse_id = os.environ.get("GOOGLE_CSE_ID") or CFG_GOOGLE_CSE_ID

    if not api_key:
        print("Error: GOOGLE_API_KEY environment variable not set.")
        return "[]"
    if not cse_id:
        print("Error: GOOGLE_CSE_ID environment variable not set.")
        return "[]"

    results_to_return = []
    try:
        from googleapiclient.discovery import build
        service = build("customsearch", "v1", developerKey=api_key)
        
        parsed_num_results = 5 # Default
        if isinstance(num_results, str):
            try:
                parsed_num_results = int(num_results)
            except ValueError:
                print(f"Warning: Google Search num_results '{num_results}' is not a valid integer string. Using default 5.")
        elif isinstance(num_results, int):
            parsed_num_results = num_results
        else:
            print(f"Warning: Google Search num_results unexpected type '{type(num_results)}'. Using default 5.")
        # Google Custom Search API allows max 10 results per query.
        # Ensure num_results is within valid range [1, 10].
        actual_num_results = max(1, min(parsed_num_results, 10))

        res = service.cse().list(q=query, cx=cse_id, num=actual_num_results).execute()
        
        if 'items' in res:
            for item in res['items']:
                results_to_return.append({
                    "title": item.get("title"),
                    "href": item.get("link"),
                    "body": item.get("snippet")
                })
    except ImportError:
        print("Error: google-api-python-client library not found. Please install it.")
        return "[]"
    except Exception as e:
        print(f"Error during Google Custom Search for query '{query}': {e}")
        return "[]"

    return json.dumps(results_to_return)

def process_search_results(search_query: str, search_results_json: str, processing_instruction: str = "answer_query") -> str:
    """
    Processes JSON search results based on a specified instruction to generate a response.

    Args:
        search_query (str): The original query that was searched.
        search_results_json (str): A JSON string containing search results (e.g., from search_duckduckgo).
                                   Expected format is a list of dictionaries, each with 'title', 'href', 'body'.
        processing_instruction (str, optional): Specifies how to process the results. Defaults to "answer_query".
            Possible values:
            - "answer_query": Formulate a direct natural language answer to the search_query.
            - "summarize_results": Provide a concise summary of the key information.
            - "extract_entities": List key entities (people, organizations, locations, etc.).
            - "custom_instruction:<your specific request>": Follow a custom instruction.

    Returns:
        str: A string containing the processed output (answer, summary, entities, custom response),
             or an error message if processing fails.
    """
    ANSWER_QUERY_LLM_PROMPT_TEMPLATE = """
Given the original search query: "{query}"
And the following search results (JSON format):
---
{results_json}
---
Based *only* on the provided search results, formulate a comprehensive, natural language answer to the original search query.
If the search results are empty or do not seem relevant to the query, state that you couldn't find a specific answer from the provided information.
Do not make up information not present in the results.
Focus on directly answering the query.
Answer:
"""

    SUMMARIZE_RESULTS_LLM_PROMPT_TEMPLATE = """
Given the original search query: "{query}"
And the following search results (JSON format):
---
{results_json}
---
Based *only* on the provided search results, provide a concise summary of the main information found that is relevant to the original search query.
If the search results are empty or do not seem relevant, state that you couldn't find enough information to summarize.
Do not make up information not present in the results.
Summary:
"""

    EXTRACT_ENTITIES_LLM_PROMPT_TEMPLATE = """
Given the original search query: "{query}"
And the following search results (JSON format):
---
{results_json}
---
Based *only* on the provided search results, list the key entities (e.g., people, organizations, locations, dates, specific terms or concepts) that are relevant to the original search query.
If the search results are empty or no distinct entities can be extracted, state that.
Format the output as a comma-separated list or a bulleted list if more appropriate.
Entities:
"""

    CUSTOM_INSTRUCTION_LLM_PROMPT_TEMPLATE = """
Given the original search query: "{query}"
And the following search results (JSON format):
---
{results_json}
---
Based *only* on the provided search results, follow this specific instruction: {custom_instruction}
If the results are insufficient to follow the instruction, state that.
Response:
"""

    try:
        if not search_results_json.strip():
            return "Error: Search results JSON string is empty. Cannot process."
        # No need to parse JSON here if the LLM handles it as a string.
    except Exception as e: # Catch other potential errors with input
        return f"Error preparing data for LLM: {e}"

    formatted_prompt = ""
    model_for_processing = get_model_for_task("summarization") # Default model, can be specialized later if needed

    if processing_instruction == "summarize_results":
        formatted_prompt = SUMMARIZE_RESULTS_LLM_PROMPT_TEMPLATE.format(
            query=search_query,
            results_json=search_results_json
        )
        print(f"process_search_results: Using SUMMARIZE_RESULTS prompt for query '{search_query}'")
    elif processing_instruction == "extract_entities":
        formatted_prompt = EXTRACT_ENTITIES_LLM_PROMPT_TEMPLATE.format(
            query=search_query,
            results_json=search_results_json
        )
        print(f"process_search_results: Using EXTRACT_ENTITIES prompt for query '{search_query}'")
    elif processing_instruction.startswith("custom_instruction:"):
        custom_instruction_text = processing_instruction.split(":", 1)[1].strip()
        if not custom_instruction_text:
            return "Error: Custom instruction is empty."
        formatted_prompt = CUSTOM_INSTRUCTION_LLM_PROMPT_TEMPLATE.format(
            query=search_query,
            results_json=search_results_json,
            custom_instruction=custom_instruction_text
        )
        print(f"process_search_results: Using CUSTOM_INSTRUCTION prompt for query '{search_query}' with instruction: '{custom_instruction_text}'")
    elif processing_instruction == "answer_query":
        formatted_prompt = ANSWER_QUERY_LLM_PROMPT_TEMPLATE.format(
            query=search_query,
            results_json=search_results_json
        )
        print(f"process_search_results: Using ANSWER_QUERY prompt for query '{search_query}'")
    else:
        return f"Error: Unknown processing_instruction: '{processing_instruction}'. Valid options are 'answer_query', 'summarize_results', 'extract_entities', or 'custom_instruction:<your_request>'."

    print(f"process_search_results: Sending prompt to LLM (model: {model_for_processing}) for query '{search_query}' with instruction '{processing_instruction}'")
    
    llm_response = invoke_ollama_model(
        formatted_prompt,
        model_name=model_for_processing,
        temperature=0.5, 
        max_tokens=1000 
    )

    if llm_response:
        return llm_response.strip()
    else:
        return f"Error: LLM failed to generate a response for the query '{search_query}' with instruction '{processing_instruction}' from the provided search results."
