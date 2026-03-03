import requests
import json
import time

BASE_URL = "http://localhost:5000"

def verify_config_api():
    print("Verifying Config API...")
    
    # 1. Test GET /api/config
    try:
        resp = requests.get(f"{BASE_URL}/api/config")
        if resp.status_code == 200:
            config = resp.json()
            print("PASS: GET /api/config")
            print(f"Current Mode: {config.get('DEFAULT_EXECUTION_MODE')}")
        else:
            print(f"FAIL: GET /api/config returned {resp.status_code}")
            return False
            
        # 2. Test POST /api/config
        original_mode = config.get('DEFAULT_EXECUTION_MODE')
        test_mode = "DIRECT" if original_mode != "DIRECT" else "THINKING_PRO"
        
        payload = {"DEFAULT_EXECUTION_MODE": test_mode}
        resp = requests.post(f"{BASE_URL}/api/config", json=payload)
        
        if resp.status_code == 200 and resp.json().get("success"):
            print(f"PASS: POST /api/config updated mode to {test_mode}")
        else:
            print(f"FAIL: POST /api/config failed: {resp.text}")
            return False
            
        # 3. Verify Persistence (GET again)
        resp = requests.get(f"{BASE_URL}/api/config")
        new_mode = resp.json().get('DEFAULT_EXECUTION_MODE')
        
        if new_mode == test_mode:
             print("PASS: Config persistence verified.")
        else:
             print(f"FAIL: Persistence check failed. Expected {test_mode}, got {new_mode}")
             return False
             
        # Cleanup (Revert)
        requests.post(f"{BASE_URL}/api/config", json={"DEFAULT_EXECUTION_MODE": original_mode})
        print("PASS: Reverted config to original state.")
        
    except Exception as e:
        print(f"Error: {e}")
        # If server isn't running, we can't fully verify end-to-end HTTP, 
        # but we can assume unit tests for ConfigManager would pass.
        print("NOTE: Ensure the web server is running for this test.")
        return False
        
    return True

if __name__ == "__main__":
    verify_config_api()
