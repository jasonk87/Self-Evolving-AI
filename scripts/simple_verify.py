import requests
from ai_assistant import config

def main():
    print("--- Simple Verification ---")
    api_key = config._get_api_key()
    print(f"Key Prefix: {api_key[:5] if api_key else 'None'}")
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.DEFAULT_MODEL}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    payload = {"contents": [{"parts": [{"text": "Hello"}]}]}
    
    response = requests.post(url, headers=headers, json=payload)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.text}")

if __name__ == "__main__":
    main()
