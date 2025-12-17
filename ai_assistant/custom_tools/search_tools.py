# ai_assistant/custom_tools/search_tools.py
import json
from googleapiclient.discovery import build
from ai_assistant.config import GOOGLE_API_KEY, GOOGLE_CSE_ID
from typing import List, Dict, Any, Optional
import asyncio

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

def execute_deep_research(query: str) -> str:
    """
    Performs 'Deep Research' by searching Google, visiting the top results, reading their content,
    and synthesizing a comprehensive answer. Use this for complex coding questions, API documentation lookups,
    or when standard snippets are insufficient.

    Args:
        query (str): The research question.

    Returns:
        str: A detailed research summary with sources.
    """
    # Lazy import to avoid circular dependencies if any
    from ai_assistant.core.deep_research import DeepResearcher

    researcher = DeepResearcher()

    # Run the async method in a synchronous wrapper (assuming this tool is called synchronously)
    # If the tool execution environment supports async, this should be awaited directly.
    # Given the context, we'll use asyncio.run or ensure a loop exists.

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        # If we are already in an event loop (likely, given this is an async app),
        # we might need to handle this differently if tools are awaited.
        # But if tools are run in a thread or executor, we can use run_coroutine_threadsafe.
        # However, looking at the codebase, tools seem to be just functions.
        # Assuming the caller can handle async if the tool is async?
        # Standard synchronous tools usually block.
        # If this is called from an async context, we cannot use asyncio.run().
        # We'll try to return a coroutine if the caller expects it, but standard tools are usually sync.
        # SAFE APPROACH: Use a nest_asyncio pattern or run in a separate thread if needed.
        # For now, let's assume we can use a fresh runner or the existing loop logic.

        # NOTE: If the caller is the Orchestrator running an async loop, calling this blocking function
        # which tries to run another loop will fail.
        # Ideally, this tool function should be async.
        # Let's check if other tools are async.
        # search_tools.py functions are sync.

        # We will wrap it in a way that works.
        import nest_asyncio
        nest_asyncio.apply()
        return loop.run_until_complete(researcher.perform_deep_research(query))["summary"]
    else:
        return asyncio.run(researcher.perform_deep_research(query))["summary"]

import requests
import os
import uuid

def web_search_images(query: str, num_images: int = 1) -> Dict[str, Any]:
    """
    Searches for images on the web, downloads them locally, and returns their paths for display.
    Use this when the user asks to "show" or "see" something.

    Args:
        query (str): The search query for the image.
        num_images (int): Number of images to return (max 5).

    Returns:
        Dict[str, Any]: A dictionary with a key 'images' containing a list of web-accessible paths 
                        (e.g., ['static/downloaded_images/img1.jpg']).
    """
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return {"error": "Google API Key or CSE ID is not configured."}

    try:
        service = build("customsearch", "v1", developerKey=GOOGLE_API_KEY)
        num_images = max(1, min(num_images, 5))
        
        # searchType='image' is the key here
        res = service.cse().list(q=query, cx=GOOGLE_CSE_ID, num=num_images, searchType='image').execute()
        
        # Determine strict path to static folder relative to this file
        # this file: ai_assistant/custom_tools/search_tools.py
        # root: ai_assistant/../..
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        static_dir = os.path.join(base_dir, 'static', 'downloaded_images')
        os.makedirs(static_dir, exist_ok=True)
        
        downloaded_paths = []
        
        if 'items' in res:
            for item in res['items']:
                link = item.get("link")
                if not link:
                    continue
                    
                try:
                    # Download content
                    img_data = requests.get(link, timeout=5).content
                    
                    # Generate unique filename
                    ext = os.path.splitext(link)[1].lower()
                    if ext not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                        ext = '.jpg' # Fallback
                        
                    filename = f"img_{uuid.uuid4()}{ext}"
                    filepath = os.path.join(static_dir, filename)
                    
                    with open(filepath, 'wb') as f:
                        f.write(img_data)
                        
                    # Add relative path for frontend (assuming Flask serves 'static' at /static)
                    # We return 'static/downloaded_images/filename'
                    downloaded_paths.append(f"static/downloaded_images/{filename}")
                    
                except Exception as e:
                    print(f"Failed to download image from {link}: {e}")
                    continue

        if not downloaded_paths:
            return {"result": f"Found images for '{query}' but failed to download them.", "images": []}
            
        return {
            "result": f"Successfully found and downloaded {len(downloaded_paths)} images for '{query}'.",
            "images": downloaded_paths
        }
            
    except Exception as e:
        print(f"An error occurred during Image Search: {e}")
        return {"error": str(e)}