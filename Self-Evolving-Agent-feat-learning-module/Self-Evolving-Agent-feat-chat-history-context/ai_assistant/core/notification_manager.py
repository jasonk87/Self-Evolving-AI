import json
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum, auto
from typing import List, Dict, Any, Optional
import asyncio # For __main__ test sleep

try:
    from ai_assistant.config import get_data_dir # Assumes this function exists
except ImportError: # pragma: no cover
    # Fallback for standalone execution or if config structure is different
    def get_data_dir():
        # This path should ideally point to a directory where data can be stored,
        # e.g., a 'data' subdirectory within 'ai_assistant/core' or a user-specific directory.
        # For this example, let's assume it's relative to this file's parent's parent.
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "core_data"))


NOTIFICATIONS_FILE_NAME = "notifications.json"

class NotificationStatus(Enum):
    UNREAD = auto()
    READ = auto()
    ARCHIVED = auto()

class NotificationType(Enum):
    # Task related
    TASK_COMPLETED_SUCCESSFULLY = auto()
    TASK_FAILED_PRE_REVIEW = auto()
    TASK_FAILED_CRITIC_REVIEW = auto()
    TASK_FAILED_POST_MOD_TEST = auto()
    TASK_FAILED_APPLY = auto()
    TASK_FAILED_CODE_GENERATION = auto() # Added
    TASK_FAILED_UNKNOWN = auto()
    TASK_CANCELLED = auto()

    # Suggestion related
    NEW_SUGGESTION_CREATED_AI = auto()
    NEW_SUGGESTION_FROM_USER_COMMAND = auto()
    SUGGESTION_APPROVED_USER = auto()
    SUGGESTION_DENIED_USER = auto()
    SUGGESTION_IMPLEMENTED = auto()

    # Self-modification specific
    SELF_MODIFICATION_APPLIED = auto()
    SELF_MODIFICATION_REJECTED_CRITICS = auto()
    SELF_MODIFICATION_FAILED_TESTS = auto()

    # General System Info
    GENERAL_INFO = auto()
    WARNING = auto()
    ERROR = auto()


@dataclass
class Notification:
    event_type: NotificationType
    summary_message: str
    notification_id: str = field(default_factory=lambda: f"notify_{uuid.uuid4().hex[:10]}")
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: NotificationStatus = NotificationStatus.UNREAD
    related_item_id: Optional[str] = None
    related_item_type: Optional[str] = None
    details_payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['event_type'] = self.event_type.name
        data['status'] = self.status.name
        data['timestamp'] = self.timestamp.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Notification':
        try:
            data['event_type'] = NotificationType[data['event_type']]
            data['status'] = NotificationStatus[data['status']]
            data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        except KeyError as e: # pragma: no cover
            raise ValueError(f"Missing or invalid enum key in notification data: {e} - Data: {data}")
        except ValueError as e: # pragma: no cover
            raise ValueError(f"Invalid datetime format in notification data: {e} - Data: {data}")
        return cls(**data)


class NotificationManager:
    def __init__(self, filepath: Optional[str] = None):
        self.filepath = filepath or os.path.join(get_data_dir(), NOTIFICATIONS_FILE_NAME)
        self.notifications: List[Notification] = []
        self._load_notifications()

    def _ensure_data_dir_exists(self):
        dir_path = os.path.dirname(self.filepath)
        if not os.path.exists(dir_path): # pragma: no cover
            os.makedirs(dir_path, exist_ok=True)

    def _load_notifications(self):
        self._ensure_data_dir_exists()
        if not os.path.exists(self.filepath):
            self.notifications = []
            return

        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                content = f.read()
                if not content:
                    self.notifications = []
                    return
                data_list = json.loads(content)
                self.notifications = [Notification.from_dict(item) for item in data_list]
                self.notifications.sort(key=lambda n: n.timestamp, reverse=True)
        except (IOError, json.JSONDecodeError, ValueError) as e: # pragma: no cover
            print(f"Error loading notifications from '{self.filepath}': {e}. Initializing with empty list.")
            self.notifications = []

    def _save_notifications(self):
        self._ensure_data_dir_exists()
        try:
            self.notifications.sort(key=lambda n: n.timestamp, reverse=True)
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump([n.to_dict() for n in self.notifications], f, indent=4)
        except IOError as e: # pragma: no cover
            print(f"Error saving notifications to '{self.filepath}': {e}")

    def add_notification(
        self,
        event_type: NotificationType,
        summary_message: str,
        related_item_id: Optional[str] = None,
        related_item_type: Optional[str] = None,
        details_payload: Optional[Dict[str, Any]] = None
    ) -> Notification:
        if len(summary_message) > 500:
            summary_message = summary_message[:497] + "..."

        new_notification = Notification(
            event_type=event_type,
            summary_message=summary_message,
            related_item_id=related_item_id,
            related_item_type=related_item_type,
            details_payload=details_payload or {}
        )
        self.notifications.insert(0, new_notification)
        self._save_notifications()
        print(f"NotificationManager: Added notification {new_notification.notification_id} ({event_type.name})")
        return new_notification

    def get_notifications(
        self,
        status_filter: Optional[NotificationStatus] = NotificationStatus.UNREAD,
        type_filter: Optional[NotificationType] = None,
        limit: int = 10
    ) -> List[Notification]:
        filtered_notifications = self.notifications
        if status_filter:
            filtered_notifications = [n for n in filtered_notifications if n.status == status_filter]
        if type_filter:
            filtered_notifications = [n for n in filtered_notifications if n.event_type == type_filter]

        return filtered_notifications[:limit]

    def _get_notification_by_id(self, notification_id: str) -> Optional[Notification]:
        for notification in self.notifications:
            if notification.notification_id == notification_id:
                return notification
        return None

    def mark_as_read(self, notification_ids: List[str]) -> bool:
        updated_count = 0
        for nid in notification_ids:
            notification = self._get_notification_by_id(nid)
            if notification and notification.status == NotificationStatus.UNREAD:
                notification.status = NotificationStatus.READ
                notification.timestamp = datetime.now(timezone.utc)
                updated_count +=1
        if updated_count > 0:
            self._save_notifications()
        return updated_count > 0

    def mark_as_archived(self, notification_ids: List[str]) -> bool:
        updated_count = 0
        for nid in notification_ids:
            notification = self._get_notification_by_id(nid)
            if notification and notification.status != NotificationStatus.ARCHIVED:
                notification.status = NotificationStatus.ARCHIVED
                notification.timestamp = datetime.now(timezone.utc)
                updated_count +=1
        if updated_count > 0:
            self._save_notifications()
        return updated_count > 0

if __name__ == '__main__': # pragma: no cover
    print("--- Testing NotificationManager ---")
    data_dir_for_test = get_data_dir()
    if not os.path.exists(data_dir_for_test):
        os.makedirs(data_dir_for_test, exist_ok=True)

    test_file = os.path.join(data_dir_for_test, "test_notifications.json")
    if os.path.exists(test_file):
        os.remove(test_file)

    manager = NotificationManager(filepath=test_file)

    print(f"Initial notifications (should be 0): {len(manager.get_notifications(status_filter=None))}")

    n1 = manager.add_notification(NotificationType.TASK_COMPLETED_SUCCESSFULLY, "Tool 'x' created.", "tool_x")
    asyncio.run(asyncio.sleep(0.01))
    n2 = manager.add_notification(NotificationType.NEW_SUGGESTION_CREATED_AI, "Suggest to improve logging.", "sugg_log")
    asyncio.run(asyncio.sleep(0.01))
    n3 = manager.add_notification(NotificationType.TASK_FAILED_CRITIC_REVIEW, "Self-mod for 'y' rejected.", "self_mod_y", details_payload={"reason": "Security concern"})

    print(f"Total notifications after add: {len(manager.get_notifications(status_filter=None))}")
    assert len(manager.get_notifications(status_filter=None)) == 3

    unread = manager.get_notifications(status_filter=NotificationStatus.UNREAD)
    print(f"Unread notifications (should be 3, newest first):")
    for n in unread: print(f"  ID: {n.notification_id}, Type: {n.event_type.name}, Msg: {n.summary_message}, Status: {n.status.name}")
    assert len(unread) == 3
    assert unread[0].notification_id == n3.notification_id

    manager.mark_as_read([n1.notification_id, n3.notification_id])
    unread_after_read = manager.get_notifications(status_filter=NotificationStatus.UNREAD)
    read_notifications = manager.get_notifications(status_filter=NotificationStatus.READ)
    print(f"Unread after mark_as_read (should be 1): {len(unread_after_read)}")
    assert len(unread_after_read) == 1
    assert unread_after_read[0].notification_id == n2.notification_id
    print(f"Read notifications (should be 2): {len(read_notifications)}")
    assert len(read_notifications) == 2

    manager.mark_as_archived([n1.notification_id])
    archived_notifications = manager.get_notifications(status_filter=NotificationStatus.ARCHIVED)
    read_after_archive = manager.get_notifications(status_filter=NotificationStatus.READ)
    print(f"Archived notifications (should be 1): {len(archived_notifications)}")
    assert len(archived_notifications) == 1
    assert archived_notifications[0].notification_id == n1.notification_id
    print(f"Read notifications after archive (should be 1): {len(read_after_archive)}")
    assert len(read_after_archive) == 1
    assert read_after_archive[0].notification_id == n3.notification_id

    print("\n--- Testing persistence ---")
    manager2 = NotificationManager(filepath=test_file)
    print(f"Loaded notifications (should be 3 total, sorted by timestamp desc): {len(manager2.get_notifications(status_filter=None))}")
    all_loaded = manager2.get_notifications(status_filter=None, limit=5)
    assert len(all_loaded) == 3
    for n_loaded in all_loaded:
        print(f"  Loaded: {n_loaded.notification_id}, Status: {n_loaded.status.name}, Time: {n_loaded.timestamp.isoformat()}")

    if len(all_loaded) == 3:
        assert all_loaded[0].notification_id == n1.notification_id # Archived, newest timestamp
        assert all_loaded[1].notification_id == n3.notification_id # Read, middle timestamp
        assert all_loaded[2].notification_id == n2.notification_id # Unread, oldest timestamp

    print(f"Notification file used: {test_file}")
    # if os.path.exists(test_file): os.remove(test_file) # Clean up for repeated tests
    print("--- NotificationManager Test Finished ---")
