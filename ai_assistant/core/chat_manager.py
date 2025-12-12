import json
import os
import uuid
import time
from typing import List, Dict, Optional

class ChatSessionManager:
    def __init__(self, storage_dir: str):
        self.storage_dir = storage_dir
        os.makedirs(self.storage_dir, exist_ok=True)

    def create_session(self, title: str = "New Chat") -> str:
        session_id = str(uuid.uuid4())
        session_data = {
            "id": session_id,
            "title": title,
            "created_at": time.time(),
            "updated_at": time.time(),
            "history": []
        }
        self._save_session(session_id, session_data)
        return session_id

    def get_session(self, session_id: str) -> Optional[Dict]:
        path = os.path.join(self.storage_dir, f"{session_id}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None

    def list_sessions(self) -> List[Dict]:
        sessions = []
        if not os.path.exists(self.storage_dir):
             return []
        for filename in os.listdir(self.storage_dir):
            if filename.endswith(".json"):
                try:
                    with open(os.path.join(self.storage_dir, filename), 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        sessions.append({
                            "id": data.get("id"),
                            "title": data.get("title", "Untitled Chat"),
                            "updated_at": data.get("updated_at", 0)
                        })
                except Exception:
                    continue
        # Sort by updated_at desc
        return sorted(sessions, key=lambda x: x['updated_at'], reverse=True)

    def add_message(self, session_id: str, role: str, content: str):
        session = self.get_session(session_id)
        if not session:
            # If session doesn't exist, create it implicitly? 
            # No, for robustness, we should create it explicitly if needed, but let's handle just in case.
            # Actually, let's create it.
            session_id = self.create_session() # Generates new ID if passed one was invalid? 
            # Wait, if I pass an ID that doesn't exist, I can't just create a random NEW one and return it easily here without changing ID.
            # Let's return None to signal failure.
            return None
        
        session["history"].append({"role": role, "content": content})
        session["updated_at"] = time.time()
        
        # Auto-update title if it's the first user message and title is "New Chat"
        # Check if there is exactly 1 user message (the one we just added)
        user_msgs = [m for m in session["history"] if m["role"] == "user"]
        if len(user_msgs) == 1 and role == "user" and session["title"] == "New Chat":
             # Simple heuristic: first few words
             # Strip newlines
             clean_content = content.strip().split('\n')[0]
             session["title"] = clean_content[:30] + ("..." if len(clean_content) > 30 else "")

        self._save_session(session_id, session)
        return session

    def delete_session(self, session_id: str) -> bool:
         path = os.path.join(self.storage_dir, f"{session_id}.json")
         if os.path.exists(path):
             os.remove(path)
             return True
         return False

    def clear_all_sessions(self):
        # Mostly for debug/testing
        if os.path.exists(self.storage_dir):
            for f in os.listdir(self.storage_dir):
                os.remove(os.path.join(self.storage_dir, f))

    def _save_session(self, session_id: str, data: Dict):
        path = os.path.join(self.storage_dir, f"{session_id}.json")
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
