# ai_assistant/custom_tools/search_tools.py
import json
from googleapiclient.discovery import build
from ai_assistant.config import GOOGLE_API_KEY, GOOGLE_CSE_ID
import ai_assistant.config as config
from typing import List, Dict, Any, Optional
import asyncio
import urllib.parse

async def google_custom_search(query: str, num_results: int = 5) -> Dict[str, Any]:
    """
    Your primary tool for finding information online using Google Search.
    Args:
        query (str): The search query.
        num_results (int): The number of search results to return (default is 5, max is 10).
    Returns:
        Dict[str, Any]: A dictionary containing 'results' (list of dicts) and 'images' (list of base64 strings).
    """
    # 1. VISUALIZATION (Ghost Mode)
    images = []
    if getattr(config, "AUTO_WEB_PIP", False) or config.GHOST_MODE:
        try:
            from ai_assistant.core.vision_service import VisionService
            vision = VisionService()
            encoded_query = urllib.parse.quote(query)
            search_url = f"https://www.google.com/search?q={encoded_query}"
            
            print(f"Ghost Mode: Visualizing search for '{query}'...")
            b64_screenshot = await vision.capture_page_screenshot(search_url)
            if b64_screenshot:
                images.append(b64_screenshot)
        except Exception as e:
            print(f"Ghost Mode Visualization Error: {e}")

    # 2. DATA RETRIEVAL (API)
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return {"results": [], "images": images, "error": "Google API Key or CSE ID is not configured."}
    try:
        def _do_search():
            with build("customsearch", "v1", developerKey=GOOGLE_API_KEY) as service:
                return service.cse().list(q=query, cx=GOOGLE_CSE_ID, num=num_results).execute()

        res = await asyncio.to_thread(_do_search)
        
        search_results = []
        if 'items' in res:
            for item in res['items']:
                search_results.append({
                    "title": item.get("title"),
                    "link": item.get("link"),
                    "snippet": item.get("snippet")
                })
            return {"results": search_results, "images": images}
        else:
            return {"results": [], "images": images}
            
    except Exception as e:
        print(f"An error occurred during Google Custom Search: {e}")
        return {"results": [], "images": images, "error": str(e)}

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

async def web_search_images(query: str, num_images: int = 1) -> Dict[str, Any]:
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
        # Ensure num_images is an int
        num_images = int(num_images)
    except Exception:
        num_images = 1

    # Ghost Mode Visualization
    if getattr(config, "AUTO_WEB_PIP", False) or config.GHOST_MODE:
        try:
            from ai_assistant.core.vision_service import VisionService
            import urllib.parse
            # Use DuckDuckGo Images for visual effect to avoid Google Captcha
            encoded_query = urllib.parse.quote(query)
            search_url = f"https://duckduckgo.com/?q={encoded_query}&iax=images&ia=images"
             # Fire and forget (awaiting briefly just to ensure snapshot starts)
            await VisionService().capture_page_screenshot(search_url)
        except Exception as e:
            print(f"Ghost Mode Visualization failed: {e}")

    try:
        # Run synchronous Google API call in a thread to avoid blocking
        def _do_image_search():
            with build("customsearch", "v1", developerKey=GOOGLE_API_KEY) as service:
                return service.cse().list(q=query, cx=GOOGLE_CSE_ID, num=max(1, min(num_images, 5)), searchType='image').execute()
                
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(None, _do_image_search)
        
        # Determine strict path to static folder relative to this file
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        static_dir = os.path.join(base_dir, 'static', 'downloaded_images')
        os.makedirs(static_dir, exist_ok=True)
        
        downloaded_paths = []
        markdown_results = []
        
        if 'items' in res:
            for item in res['items']:
                link = item.get("link")
                if not link:
                    continue
                    
                try:
                    # Download content (in executor to avoid blocking)
                    img_data = await loop.run_in_executor(None, lambda: requests.get(link, timeout=5).content)
                    
                    # Generate unique filename
                    ext = os.path.splitext(link)[1].lower()
                    if ext not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                        ext = '.jpg' # Fallback
                        
                    filename = f"img_{uuid.uuid4()}{ext}"
                    filepath = os.path.join(static_dir, filename)
                    
                    # Write file
                    with open(filepath, 'wb') as f:
                        f.write(img_data)
                        
                    # Web accessible path (relative to static)
                    web_path = f"static/downloaded_images/{filename}"
                    downloaded_paths.append(web_path)
                    markdown_results.append(f"![{query}]({web_path})")
                    
                except Exception as e:
                    print(f"Failed to download image from {link}: {e}")
                    continue

        if not downloaded_paths:
            return {"result": f"Found images for '{query}' but failed to download them.", "images": []}
            
        # Return a result string that includes the Markdown so the AI can just repeat it
        return {
            "result": f"Successfully found images for '{query}':\n\n" + "\n".join(markdown_results),
            "images": downloaded_paths
        }
            
    except Exception as e:
        print(f"An error occurred during Image Search: {e}")
        return {"error": str(e)}