
import requests
import json
import uuid

BASE_URL = "http://localhost:5000"

def test_add_session_and_summarize():
    print("--- Testing Session Summarization ---")
    
    # 1. Create a dummy session
    session_res = requests.post(f"{BASE_URL}/api/sessions", json={"title": "Test Session for Summarization"})
    if session_res.status_code != 200:
        print("Failed to create session:", session_res.text)
        return
    
    session_id = session_res.json().get("session_id")
    print(f"Created Session: {session_id}")

    # 2. Add some messages
    messages = [
        {"role": "user", "content": "I am planning a trip to Mars."},
        {"role": "assistant", "content": "That sounds exciting! What is your timeline?"},
        {"role": "user", "content": "I want to leave in 2030."},
        {"role": "assistant", "content": "You will need a very reliable rocket."},
        {"role": "user", "content": "Yes, I am looking at Starship."}
    ]

    for msg in messages:
        # We need to manually inject into chat manager or use /chat endpoint.
        # Using /chat endpoint might trigger LLM which we want to avoid for speed if possible, 
        # but the chat endpoint is the main way to add messages to history.
        # Alternatively, we can use the internal ChatManager if we run this as a script within the app context,
        # but testing via API is more realistic.
        # However, calling /chat triggers the Orchestrator. 
        # Let's just mock the history by modifying the session JSON file directly?
        # No, that's brittle.
        # Let's use the `add_message` method of ChatManager via a small script we execute?
        # Or just use the `/chat` endpoint and ignore the AI response key?
        # Actually, let's just create a raw session file to simulate history if we want to isolate.
        pass
    
    # Wait. The `ChatSessionManager` stores data in JSON.
    # It's easier to verify the "summarize" endpoint if we have data.
    # Let's try to hit the summarize endpoint on an EXISTING session if one exists?
    # Or create one using internal python code.
    
    pass

if __name__ == "__main__":
    # We will write a script that imports the app modules to populate data quickly without relying on the live server for chat generation.
    import sys
    import os
    sys.path.append(os.getcwd())
    
    from ai_assistant.core.chat_manager import ChatSessionManager
    
    # Setup
    chat_manager = ChatSessionManager(os.path.join(os.getcwd(), "_memory_", "chat_sessions"))
    session_id = chat_manager.create_session(title="Mars Trip Verification")
    
    print(f"Created local session: {session_id}")
    
    # Add history manually
    chat_manager.add_message(session_id, "user", "I want to build a base on Mars.")
    chat_manager.add_message(session_id, "assistant", "That is a bold goal. What materials will you use?")
    chat_manager.add_message(session_id, "user", "Regolith 3D printing.")
    chat_manager.add_message(session_id, "assistant", "Excellent choice for in-situ resource utilization.")
    
    print("Added mock history.")
    
    # Now call the API to summarize
    print(f"Triggering summarization via API for session {session_id}...")
    try:
        res = requests.post(f"{BASE_URL}/api/sessions/{session_id}/summarize")
        if res.status_code == 200:
            data = res.json()
            if data.get("success"):
                episode = data.get("episode")
                print("\n[SUCCESS] Summarization Complete!")
                print("Episode ID:", episode.get("episode_id"))
                print("Title:", episode.get("title"))
                print("Summary:", episode.get("summary"))
                print("Topics:", episode.get("key_topics"))
            else:
                print("\n[FAILURE] API returned success=False:", data)
        else:
            print(f"\n[FAILURE] API Request Failed: {res.status_code} {res.text}")
            
    except Exception as e:
        print(f"\n[ERROR] Request failed: {e}")

