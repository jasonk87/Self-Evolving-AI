# ai_assistant/custom_tools/search_tools.py
import json
from googleapiclient.discovery import build
from ai_assistant.config import GOOGLE_API_KEY, GOOGLE_CSE_ID
from typing import List, Dict, Any, Optional

def google_custom_search(query: str, num_results: int = 5) -> Optional[List[Dict[str, Any]]]:
    """
    Your primary tool for finding information online using Google Search. Use this for questions
    like 'what is X?', 'who is Y?', 'explain Z', 'search for A', 'find information on B',
    or when you need current, up-to-date facts and details.
    Args:
        query (str): The search query.
        num_results (int): The number of search results to return (default is 5, max is 10).
    Returns:
        Optional[List[Dict[str, Any]]]: A list of search results, or None if an error occurs or keys are missing.
    """
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        print("Error: Google API Key or CSE ID is not configured.")
        return None
    try:
        service = build("customsearch", "v1", developerKey=GOOGLE_API_KEY)
        # Clamp num_results between 1 and 10 (API limit per request)
        num_results = max(1, min(num_results, 10))
        
        res = service.cse().list(q=query, cx=GOOGLE_CSE_ID, num=num_results).execute()
        
        search_results = []
        if 'items' in res:
            for item in res['items']:
                search_results.append({
                    "title": item.get("title"),
                    "link": item.get("link"),
                    "snippet": item.get("snippet")
                })
            return search_results
        else:
            return [] # No items found
            
    except Exception as e:
        print(f"An error occurred during Google Custom Search: {e}")
        # Potentially log the error in more detail
        return None