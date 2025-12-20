import asyncio
import os
import sys
# Add project root to sys.path
sys.path.append(os.getcwd())

from ai_assistant.custom_tools.search_tools import web_search_images
from ai_assistant.custom_tools.my_extra_tools import process_search_results, search_duckduckgo

async def test_image_search():
    print("\n--- Testing web_search_images ---")
    query = "1969 Camaro"
    # Test with integer
    print(f"Query: {query}, num_images=1 (int)")
    try:
        res = await web_search_images(query, num_images=1)
        print(f"Result (int): {res.keys()}")
        if 'error' in res:
            print(f"Error: {res['error']}")
    except Exception as e:
        print(f"CRASH (int): {e}")

    # Test with string (simulating LLM input)
    print(f"Query: {query}, num_images='1' (str)")
    try:
        res = await web_search_images(query, num_images="1")
        print(f"Result (str): {res.keys()}")
    except Exception as e:
        print(f"CRASH (str): {e}")

async def test_process_search():
    print("\n--- Testing process_search_results ---")
    # Test with empty JSON
    res = process_search_results("test query", "[]", "answer_query")
    print(f"Empty JSON Result: {res}")
    
    # Test with valid JSON
    valid_json = '[{"title": "Test", "snippet": "This is a test result."}]'
    res = process_search_results("test query", valid_json, "answer_query")
    print(f"Valid JSON Result length: {len(res)}")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(test_image_search())
    test_process_search()
