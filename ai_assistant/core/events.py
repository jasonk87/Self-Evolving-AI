from typing import Callable, Any, Dict, List

class EventEmitter:
    _instance = None
    _listeners: List[Callable[[str, Dict[str, Any]], None]] = []

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(EventEmitter, cls).__new__(cls)
            cls._instance._listeners = []
        return cls._instance

    @classmethod
    def register_listener(cls, listener: Callable[[str, Dict[str, Any]], None]):
        if cls._instance is None:
            cls()
        cls._instance._listeners.append(listener)

    @classmethod
    def emit(cls, event_name: str, data: Dict[str, Any]):
        if cls._instance is None:
            return
        for listener in cls._instance._listeners:
            try:
                listener(event_name, data)
            except Exception as e:
                print(f"Error in event listener: {e}")

def emit_system_event(event_name: str, data: Dict[str, Any]):
    """Helper function to emit events globally."""
    EventEmitter.emit(event_name, data)
