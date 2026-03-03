import os

def check_config_static():
    print("Static Verification of Config...")
    file_path = 'ai_assistant/config.py'
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        if 'DEFAULT_EXECUTION_MODE = "THINKING_PRO"' in content:
            print("PASS: DEFAULT_EXECUTION_MODE set to THINKING_PRO")
        else:
             print("FAIL: DEFAULT_EXECUTION_MODE not set correctly.")
             return False

        if '"default": "PARALLEL"' in content:
            print("PASS: default reasoning set to PARALLEL")
        else:
             print("FAIL: default reasoning not set correctly.")
             return False

    except Exception as e:
        print(f"Error reading file: {e}")
        return False
        
    print("Verification Successful.")
    return True

if __name__ == "__main__":
    check_config_static()
