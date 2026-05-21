from datetime import date, datetime
import re
from typing import Any
from typing import Union
from duckduckgo_search import DDGS
import json
from typing import Optional, Union, List, Dict, Any

from ai_assistant.config import get_model_for_task, GOOGLE_API_KEY as CFG_GOOGLE_API_KEY, GOOGLE_CSE_ID as CFG_GOOGLE_CSE_ID
import os

CURRENT_NEWS_TERMS = (
    "today",
    "latest",
    "current",
    "breaking",
    "headline",
    "headlines",
    "news",
    "right now",
    "this morning",
    "this afternoon",
    "this evening",
)


def _format_search_date(value: date | None = None) -> str:
    value = value or datetime.now().date()
    return f"{value:%B} {value.day}, {value:%Y}"


def _is_current_news_query(query: str) -> bool:
    query_lower = str(query or "").lower()
    return any(term in query_lower for term in CURRENT_NEWS_TERMS)


def _enrich_current_news_query(query: str, today: date | None = None) -> str:
    """
    Anchor current/news searches to today's date so stale result pages are less likely
    to be treated as fresh reporting.
    """
    if not _is_current_news_query(query):
        return query
    formatted_date = _format_search_date(today)
    if formatted_date.lower() in query.lower():
        return query
    if re.search(r"\b(20\d{2}|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b", query, re.IGNORECASE):
        return query
    return f"{query} {formatted_date}"


def _current_news_result_guidance(query: str) -> str:
    if not _is_current_news_query(query):
        return ""

    today = _format_search_date()
    return (
        f"\nCurrent-news freshness rules:\n"
        f"- Today's date is {today}.\n"
        "- Treat this as a current-news request.\n"
        "- Only present an item as today's news if the title, snippet, or source text clearly supports that it is current "
        "or from roughly the last 24-48 hours.\n"
        "- If a result is undated, old, historical, or ambiguous, do not summarize it as today's news.\n"
        "- Mention source names/links when available, and say when freshness cannot be verified from the provided results.\n"
    )

def subtract_numbers(a: float, b: float) -> Union[float, str]:
    """Subtracts the second number from the first."""
    try:
        return float(a) - float(b)
    except ValueError:
        return "Error: Invalid input. 'a' and 'b' must be numbers for subtract_numbers."

def get_current_time() -> str:
    """
    Returns the current date and time.
    """
    from datetime import datetime
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def echo_message(message: str, num_repeats: int=1) -> str:
    """
    Repeats a message a specified number of times.
    
    Args:
        message (str): The message to repeat.
        num_repeats (int): Number of times to repeat (default 1).
    """
    try:
        count = int(num_repeats)
        if count < 0:
            return 'Error: repeat count cannot be negative.'
    except ValueError:
        return 'Error: repeat count must be an integer.'
    return ' '.join([str(message)] * count)

async def search_duckduckgo(*args, **kwargs) -> str:
    """
    Searches the internet using DuckDuckGo and returns the results as a JSON string.
    Current/news-style queries are automatically anchored to today's date and ask
    DuckDuckGo for day-limited results when supported.
    
    Args:
        *args: Positional arguments. The first one is taken as the query.
        **kwargs: Keyword arguments. 'query' is checked here.

    Returns:
        str: A JSON string representing a list of search results.
    """
    import json
    from duckduckgo_search import DDGS
    from ai_assistant.custom_tools.my_extra_tools import search_google_custom_search
    query: Optional[str] = None
    if 'query' in kwargs:
        query = str(kwargs['query'])
    elif args:
        query = str(args[0])
    if not query:
        print('Error: search_duckduckgo requires a query argument.')
        return '[]'
    search_query = _enrich_current_news_query(query)
    is_current_news = _is_current_news_query(query)
    try:
        max_results = int(kwargs.get("num_results", 5))
    except (TypeError, ValueError):
        max_results = 5
    max_results = max(1, min(max_results, 10))
    results = []
    
    # Ghost Mode Visualization
    images = []
    from ai_assistant import config
    if getattr(config, "AUTO_WEB_PIP", False) or config.GHOST_MODE:
        try:
            from ai_assistant.core.vision_service import VisionService
            import urllib.parse
            encoded_query = urllib.parse.quote(search_query)
            search_url = f"https://duckduckgo.com/?q={encoded_query}"
            b64_screenshot = await VisionService().capture_page_screenshot(search_url)
            if b64_screenshot:
                images.append(b64_screenshot)
        except Exception as e:
            print(f"Ghost Mode Visualization failed in search_duckduckgo: {e}")

    try:
        # Run synchronous DDGS in thread
        import asyncio
        with DDGS() as ddgs:
            # DDGS is sync, so we wrap the call
            def run_ddgs():
                ddgs_kwargs = {"max_results": max_results, "backend": "html"}
                if is_current_news:
                    ddgs_kwargs["timelimit"] = "d"
                try:
                    search_results = ddgs.text(search_query, **ddgs_kwargs)
                except TypeError:
                    ddgs_kwargs.pop("timelimit", None)
                    search_results = ddgs.text(search_query, **ddgs_kwargs)
                if not search_results:
                    fallback_kwargs = {"max_results": max_results}
                    if is_current_news:
                        fallback_kwargs["timelimit"] = "d"
                    try:
                        search_results = ddgs.text(search_query, **fallback_kwargs)
                    except TypeError:
                        fallback_kwargs.pop("timelimit", None)
                        search_results = ddgs.text(search_query, **fallback_kwargs)
                return search_results
            
            search_results = await asyncio.to_thread(run_ddgs)
            
        if search_results:
            for r in search_results:
                if isinstance(r, dict) and 'title' in r and ('href' in r) and ('body' in r):
                    results.append({'title': r['title'], 'href': r['href'], 'body': r['body']})
    except Exception as e:
        print(f"Error during DuckDuckGo search for query '{search_query}': {e}")
        
    if not results:
        print('DuckDuckGo returned no results. Attempting Google Custom Search fallback...')
        try:
            google_res_data = await search_google_custom_search(search_query, num_results=max_results)
            if google_res_data and isinstance(google_res_data, dict):
                fallback_results = google_res_data.get('results', '[]')
                fallback_images = google_res_data.get('images', [])
                images.extend(fallback_images)
                return {"results": fallback_results, "images": images, "query_used": search_query, "queried_at": datetime.now().isoformat()}
            elif isinstance(google_res_data, str) and google_res_data.strip():
                return {"results": google_res_data, "images": images, "query_used": search_query, "queried_at": datetime.now().isoformat()}
        except Exception as e:
            print(f'Google fallback failed: {e}')
            
    if not results:
        err_res = json.dumps([{'title': 'Search Failed', 'href': '#', 'body': 'Could not retrieve search results from DuckDuckGo or Google. Verify internet connection or configure GOOGLE_API_KEY and GOOGLE_CSE_ID.'}])
        return {"results": err_res, "images": images, "query_used": search_query, "queried_at": datetime.now().isoformat()}
        
    return {"results": json.dumps(results), "images": images, "query_used": search_query, "queried_at": datetime.now().isoformat()}

async def search_web(*args, **kwargs) -> str:
    """
    Searches the web using the assistant's resilient search path.

    Alias for search_duckduckgo; use this for current news, facts, and general web lookup.
    """
    return await search_duckduckgo(*args, **kwargs)

async def web_search(*args, **kwargs) -> str:
    """
    Searches the web using the assistant's resilient search path.

    Alias for search_duckduckgo; use this for current news, facts, and general web lookup.
    """
    return await search_duckduckgo(*args, **kwargs)

async def google_search(*args, **kwargs) -> str:
    """
    Searches the web for current information.

    Compatibility alias for search_duckduckgo with Google Custom Search fallback.
    """
    return await search_duckduckgo(*args, **kwargs)

async def news_search(query: str='top news headlines today', num_results: Union[int, str]=5) -> str:
    """
    Searches for current news with today's date and freshness metadata.

    Use this for requests like "what is on the news today", "latest headlines",
    or "breaking news right now".
    """
    return await search_duckduckgo(query=query, num_results=num_results)

async def search_google_custom_search(query: str, num_results: Union[int, str]=5) -> str:
    """
    Wrapper for the primary Google Custom Search tool. 
    Redirects to ai_assistant.custom_tools.search_tools.google_custom_search to utilize new Ghost Mode logic.
    """
    import json
    from ai_assistant.custom_tools.search_tools import google_custom_search as primary_google_search
    
    try:
        # Parse num_results
        parsed_num = 5
        if isinstance(num_results, str) and num_results.isdigit():
            parsed_num = int(num_results)
        elif isinstance(num_results, int):
            parsed_num = num_results

        # Call the primary async tool
        primary_res = await primary_google_search(query, num_results=parsed_num)
        
        results_list = primary_res.get('results', [])
        images = primary_res.get('images', [])
            
        mapped_results = []
        for item in results_list:
            mapped_results.append({
                'title': item.get('title'),
                'href': item.get('link'),
                'body': item.get('snippet')
            })
            
        return {"results": json.dumps(mapped_results), "images": images}
        
    except Exception as e:
        print(f"Error in Google Search wrapper: {e}")
        return {"results": "[]", "images": []}

from ai_assistant.llm_interface.gemini_client import invoke_gemini_model

def process_search_results(search_query: str, search_results_json: str='[]', processing_instruction: str='answer_query', **kwargs) -> str:
    """
    Processes JSON search results based on a specified instruction to generate a response using Gemini.
    """
    if processing_instruction == 'answer_query' and 'instruction' in kwargs:
        print(f"process_search_results: Warning - 'instruction' argument used instead of 'processing_instruction'. Adapting...")
        processing_instruction = kwargs['instruction']
    
    # Prompt Templates
    freshness_guidance = _current_news_result_guidance(search_query)
    ANSWER_QUERY_LLM_PROMPT_TEMPLATE = '\nGiven the original search query: "{query}"\nAnd the following search results (JSON format):\n---\n{results_json}\n---\n{freshness_guidance}\nBased *only* on the provided search results, formulate a comprehensive, natural language answer to the original search query.\nIf the search results are empty or do not seem relevant to the query, state that you couldn\'t find a specific answer from the provided information.\nDo not make up information not present in the results.\nFocus on directly answering the query.\nAnswer:\n'
    SUMMARIZE_RESULTS_LLM_PROMPT_TEMPLATE = '\nGiven the original search query: "{query}"\nAnd the following search results (JSON format):\n---\n{results_json}\n---\n{freshness_guidance}\nBased *only* on the provided search results, provide a concise summary of the main information found that is relevant to the original search query.\nIf the search results are empty or do not seem relevant, state that you couldn\'t find enough information to summarize.\nDo not make up information not present in the results.\nSummary:\n'
    EXTRACT_ENTITIES_LLM_PROMPT_TEMPLATE = '\nGiven the original search query: "{query}"\nAnd the following search results (JSON format):\n---\n{results_json}\n---\n{freshness_guidance}\nBased *only* on the provided search results, list the key entities (e.g., people, organizations, locations, dates, specific terms or concepts) that are relevant to the original search query.\nIf the search results are empty or no distinct entities can be extracted, state that.\nFormat the output as a comma-separated list or a bulleted list if more appropriate.\nEntities:\n'
    CUSTOM_INSTRUCTION_LLM_PROMPT_TEMPLATE = '\nGiven the original search query: "{query}"\nAnd the following search results (JSON format):\n---\n{results_json}\n---\n{freshness_guidance}\nBased *only* on the provided search results, follow this specific instruction: {custom_instruction}\nIf the results are insufficient to follow the instruction, state that.\nResponse:\n'
    
    try:
        if not search_results_json.strip() or search_results_json == '[]':
            return 'No relevant information found in the search results. Please try a different query.'
    except Exception as e:
        return f'Error preparing data for LLM: {e}'
        
    formatted_prompt = ''
    # Using 'summarization' task hints for config if needed, but Gemini handles all.
    
    if processing_instruction == 'summarize_results':
        formatted_prompt = SUMMARIZE_RESULTS_LLM_PROMPT_TEMPLATE.format(query=search_query, results_json=search_results_json, freshness_guidance=freshness_guidance)
        print(f"process_search_results: Using SUMMARIZE_RESULTS prompt for query '{search_query}'")
    elif processing_instruction == 'extract_entities':
        formatted_prompt = EXTRACT_ENTITIES_LLM_PROMPT_TEMPLATE.format(query=search_query, results_json=search_results_json, freshness_guidance=freshness_guidance)
        print(f"process_search_results: Using EXTRACT_ENTITIES prompt for query '{search_query}'")
    elif processing_instruction.startswith('custom_instruction:'):
        custom_instruction_text = processing_instruction.split(':', 1)[1].strip()
        if not custom_instruction_text:
            return 'Error: Custom instruction is empty.'
        formatted_prompt = CUSTOM_INSTRUCTION_LLM_PROMPT_TEMPLATE.format(query=search_query, results_json=search_results_json, freshness_guidance=freshness_guidance, custom_instruction=custom_instruction_text)
        print(f"process_search_results: Using CUSTOM_INSTRUCTION prompt for query '{search_query}' with instruction: '{custom_instruction_text}'")
    elif processing_instruction == 'answer_query':
        formatted_prompt = ANSWER_QUERY_LLM_PROMPT_TEMPLATE.format(query=search_query, results_json=search_results_json, freshness_guidance=freshness_guidance)
        print(f"process_search_results: Using ANSWER_QUERY prompt for query '{search_query}'")
    elif ' ' in processing_instruction or len(processing_instruction) > 20:
        # Implicit custom instruction
        formatted_prompt = CUSTOM_INSTRUCTION_LLM_PROMPT_TEMPLATE.format(query=search_query, results_json=search_results_json, freshness_guidance=freshness_guidance, custom_instruction=processing_instruction)
        print(f"process_search_results: Using implicit CUSTOM_INSTRUCTION for query '{search_query}' with instruction: '{processing_instruction}'")
    else:
        return f"Error: Unknown instruction: '{processing_instruction}'. Valid options are 'answer_query', 'summarize_results', 'extract_entities', or 'custom_instruction:<your_request>'."

    print(f"process_search_results: Sending prompt to Gemini for query '{search_query}'...")
    
    try:
        # Use sync invoke_gemini_model since this tool is sync
        llm_response = invoke_gemini_model(formatted_prompt, temperature=0.5, max_tokens=1000)
        return llm_response.strip()
    except Exception as e:
        return f"Error: LLM (Gemini) failed to generate a response: {e}"
