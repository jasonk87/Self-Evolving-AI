
def check_session_summary_logic():
    print("Static Verification of Session Summary Logic...")
    
    file_path = 'ai_assistant/core/orchestrator.py'
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Check for key methods and calls
        has_method = 'async def _update_session_summary' in content
        has_call = '_update_session_summary(session_id' in content
        has_update_episode = 'self.memory_manager.update_episode' in content
        has_add_episode = 'self.memory_manager.add_episode' in content
        
        if has_method and has_call:
            print("PASS: _update_session_summary method and call found.")
        else:
            print(f"FAIL: Missing method or call. Method: {has_method}, Call: {has_call}")
            return False

        if has_update_episode and has_add_episode:
             print("PASS: update_episode and add_episode usage found.")
        else:
             print(f"FAIL: Logic missing memory calls. Update: {has_update_episode}, Add: {has_add_episode}")
             return False

        # Additional check: Check MemoryManager file for update_episode
        with open('ai_assistant/core/memory_manager.py', 'r', encoding='utf-8') as f:
             mem_content = f.read()
             if 'def update_episode' in mem_content:
                 print("PASS: update_episode defined in MemoryManager.")
             else:
                 print("FAIL: update_episode missing in MemoryManager.")
                 return False

    except Exception as e:
        print(f"Error reading files: {e}")
        return False
        
    print("Verification Successful.")
    return True

if __name__ == "__main__":
    check_session_summary_logic()
