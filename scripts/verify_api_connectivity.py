import sys
import os
import requests
import asyncio

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai_assistant import config

def list_available_models():
    print("\n--- Listing Available Models ---")
    api_key = config._get_api_key()
    if not api_key:
        print("[FAILURE] No API Key.")
        return

    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            models = data.get('models', [])
            print(f"Found {len(models)} models.")
            for m in models:
                print(f"- {m['name']} ({'Embed' if 'embedContent' in m.get('supportedGenerationMethods', []) else 'Generate'})")
                # Check for specific embedding support
                if "embedContent" in m.get('supportedGenerationMethods', []):
                     print("  > SUPPORTED: Embedding")
        else:
            print(f"[FAILURE] List Models Failed: {response.status_code}")
            print(response.text)
    except Exception as e:
        print(f"[FAILURE] List Models Error: {e}")

async def test_specific_embedding_model(model_name):
    print(f"\n--- Testing Embedding Model: {model_name} ---")
    api_key = config._get_api_key()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:embedContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "content": {
            "parts": [{"text": "Testing embedding access."}]
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            print(f"[SUCCESS] {model_name} works!")
        else:
            print(f"[FAILURE] {model_name} failed: {response.status_code}")
            print(response.text)
    except Exception as e:
        print(f"[FAILURE] Error: {e}")

def main():
    list_available_models()
    # Test common embedding models explicitly
    asyncio.run(test_specific_embedding_model("text-embedding-004"))
    asyncio.run(test_specific_embedding_model("embedding-001"))

if __name__ == "__main__":
    main()
