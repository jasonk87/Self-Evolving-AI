from duckduckgo_search import DDGS
import json

def test_ddg():
    query = "weather tomorrow"
    print(f"Testing query: {query}")
    try:
        with DDGS() as ddgs:
            results = ddgs.text(query, max_results=5, backend='html')
            print(f"Results type (html backend): {type(results)}")
            results_list = list(results)
            print(f"Results count: {len(results_list)}")
            print(json.dumps(results_list, indent=2))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_ddg()
