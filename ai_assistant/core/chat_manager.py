import json
import os
import uuid
import time
from typing import List, Dict, Optional, Any

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

    def add_message(self, session_id: str, role: str, content: str, images: Optional[List[str]] = None):
        session = self.get_session(session_id)
        if not session:
            # If session doesn't exist, create it implicitly? 
            # No, for robustness, we should create it explicitly if needed, but let's handle just in case.
            # Actually, let's create it.
            session_id = self.create_session() # Generates new ID if passed one was invalid? 
            # Wait, if I pass an ID that doesn't exist, I can't just create a random NEW one and return it easily here without changing ID.
            # Let's return None to signal failure.
            return None
        
        message_data = {"role": role, "content": content}
        if images:
            message_data["images"] = images

        session["history"].append(message_data)
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

    def update_session_metadata(self, session_id: str, metadata: Dict[str, Any]) -> Optional[Dict]:
        """
        Updates arbitrary metadata for a session.
        """
        session = self.get_session(session_id)
        if not session:
            return None
            
        # Ensure metadata dict exists
        if "metadata" not in session:
            session["metadata"] = {}
            
        session["metadata"].update(metadata)
        session["updated_at"] = time.time()
        
        self._save_session(session_id, session)
        return session

    def _save_session(self, session_id: str, data: Dict):
        path = os.path.join(self.storage_dir, f"{session_id}.json")
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)



    def _get_session_pointers_path(self) -> str:
        return os.path.join(self.storage_dir, "session_pointers.json")

    def _load_session_pointers(self) -> Dict[str, str]:
        path = self._get_session_pointers_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_session_pointers(self, pointers: Dict[str, str]):
        path = self._get_session_pointers_path()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(pointers, f, indent=2)

    def list_identity_pointers(self, limit: int = 100) -> List[Dict[str, Any]]:
        pointers = self._load_session_pointers()
        items: List[Dict[str, Any]] = []
        for identity_key, session_id in pointers.items():
            session_exists = bool(session_id and self.get_session(session_id))
            items.append({
                "identity_key": identity_key,
                "session_id": session_id,
                "session_exists": session_exists,
            })
        return sorted(items, key=lambda i: str(i.get("identity_key", "")))[:max(0, int(limit))]

    def prune_invalid_identity_pointers(self) -> int:
        pointers = self._load_session_pointers()
        original_count = len(pointers)
        pruned = {
            key: sid
            for key, sid in pointers.items()
            if sid and self.get_session(sid)
        }
        self._save_session_pointers(pruned)
        return max(0, original_count - len(pruned))

    def get_identity_pointer_summary(self) -> Dict[str, Any]:
        pointers = self._load_session_pointers()
        by_platform: Dict[str, int] = {}
        for key in pointers.keys():
            key_str = str(key)
            if ':' in key_str:
                platform = key_str.split(':', 1)[0] or 'unknown'
            else:
                platform = 'unknown'
            by_platform[platform] = by_platform.get(platform, 0) + 1

        return {
            "total": len(pointers),
            "by_platform": dict(sorted(by_platform.items())),
        }

    def get_session_for_identity(self, identity_key: str) -> Optional[str]:
        if not identity_key:
            return None
        pointers = self._load_session_pointers()
        session_id = pointers.get(identity_key)
        if session_id and self.get_session(session_id):
            return session_id
        return None

    def get_or_create_session_for_identity(self, identity_key: str, title: str = "New Chat") -> str:
        if not identity_key:
            return self.create_session(title=title)

        pointers = self._load_session_pointers()
        session_id = pointers.get(identity_key)
        if session_id and self.get_session(session_id):
            return session_id

        session_id = self.create_session(title=title)
        pointers[identity_key] = session_id
        self._save_session_pointers(pointers)
        return session_id

    def rotate_session_for_identity(self, identity_key: str, title: str = "New Chat") -> str:
        if not identity_key:
            return self.create_session(title=title)

        pointers = self._load_session_pointers()
        session_id = self.create_session(title=title)
        pointers[identity_key] = session_id
        self._save_session_pointers(pointers)
        return session_id

    def _get_notices_path(self) -> str:
        return os.path.join(self.storage_dir, "notices.json")

    def _load_notices(self) -> Dict[str, List[Dict[str, Any]]]:
        path = self._get_notices_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_notices(self, notices: Dict[str, List[Dict[str, Any]]]):
        path = self._get_notices_path()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(notices, f, indent=2)


    def _compute_notice_state(self, item: Dict[str, Any], now_ts: Optional[float] = None) -> str:
        now = float(now_ts if now_ts is not None else time.time())
        if item.get("resolved_at"):
            return "resolved"
        snoozed_until = item.get("snoozed_until")
        try:
            snoozed_until_val = float(snoozed_until) if snoozed_until is not None else None
        except (TypeError, ValueError):
            snoozed_until_val = None
        if snoozed_until_val and snoozed_until_val > now:
            return "snoozed"
        if item.get("read"):
            return "acknowledged"
        return "open"

    def add_user_notice(self,
                        user_scope: str,
                        message: str,
                        notice_type: str = "delegated_work",
                        source_session_id: Optional[str] = None,
                        task_id: Optional[str] = None,
                        status: Optional[str] = None,
                        metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        notices = self._load_notices()
        scope = user_scope or "local_default"
        entries = list(notices.get(scope, []))
        item = {
            "id": str(uuid.uuid4())[:8],
            "created_at": time.time(),
            "updated_at": time.time(),
            "read": False,
            "resolved_at": None,
            "snoozed_until": None,
            "type": notice_type,
            "message": message,
            "source_session_id": source_session_id,
            "task_id": task_id,
            "status": status,
            "metadata": metadata or {},
        }
        entries.append(item)
        notices[scope] = entries[-200:]
        self._save_notices(notices)
        return item

    def list_user_notices(self,
                          user_scope: str,
                          include_read: bool = False,
                          limit: int = 20,
                          state: Optional[str] = None) -> List[Dict[str, Any]]:
        notices = self._load_notices()
        scope = user_scope or "local_default"
        entries = list(notices.get(scope, []))
        filtered = entries if include_read else [n for n in entries if not n.get("read")]

        normalized_state = str(state or "").strip().lower()
        if normalized_state:
            filtered = [n for n in filtered if self._compute_notice_state(n) == normalized_state]

        items = sorted(filtered, key=lambda n: n.get("created_at", 0), reverse=True)[:limit]
        return [dict(item, notice_state=self._compute_notice_state(item)) for item in items]

    def mark_user_notices_read(self, user_scope: str, notice_ids: Optional[List[str]] = None) -> int:
        notices = self._load_notices()
        scope = user_scope or "local_default"
        entries = list(notices.get(scope, []))
        ids = set(notice_ids or [])
        changed = 0
        for item in entries:
            should_mark = (not notice_ids) or (item.get("id") in ids)
            if should_mark and not item.get("read"):
                item["read"] = True
                item["updated_at"] = time.time()
                changed += 1
        notices[scope] = entries
        self._save_notices(notices)
        return changed


    def update_user_notice_state(self,
                                 user_scope: str,
                                 notice_id: str,
                                 action: str,
                                 snooze_seconds: Optional[int] = None) -> Optional[Dict[str, Any]]:
        notices = self._load_notices()
        scope = user_scope or "local_default"
        entries = list(notices.get(scope, []))
        now = time.time()

        normalized_action = str(action or "").strip().lower()
        for item in entries:
            if str(item.get("id")) != str(notice_id):
                continue

            if normalized_action == "ack":
                item["read"] = True
            elif normalized_action == "resolve":
                item["read"] = True
                item["resolved_at"] = now
                item["snoozed_until"] = None
            elif normalized_action == "reopen":
                item["read"] = False
                item["resolved_at"] = None
                item["snoozed_until"] = None
            elif normalized_action == "snooze":
                seconds = max(1, int(snooze_seconds or 3600))
                item["snoozed_until"] = now + seconds
            else:
                return None

            item["updated_at"] = now
            item["notice_state"] = self._compute_notice_state(item, now_ts=now)
            notices[scope] = entries
            self._save_notices(notices)
            return item

        return None
