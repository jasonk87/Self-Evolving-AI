import asyncio
import logging
from typing import Dict, Any, List, Callable, Awaitable, Set
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class BlackboardEvent:
    def __init__(self, topic: str, source_agent: str, data: Dict[str, Any]):
        self.event_id = str(uuid.uuid4())
        self.topic = topic
        self.source_agent = source_agent
        self.data = data
        self.timestamp = datetime.now(timezone.utc)

    def __repr__(self):
        return f"<BlackboardEvent topic='{self.topic}' from='{self.source_agent}'>"


class Blackboard:
    """
    Shared memory space and Pub/Sub event bus for the Agent Sub-Swarm.
    Agents use this to share drafts, post test results, and signal completion.
    """
    def __init__(self, swarm_id: str):
        self.swarm_id = swarm_id
        # Shared State (Memory Space)
        self.state: Dict[str, Any] = {
            "artifacts": {}, # e.g., "auth_service.py": "def login()..."
            "test_results": {},
            "status": "initializing"
        }

        # Pub/Sub Infrastructure
        # topic -> list of async callback functions
        self.subscribers: Dict[str, List[Callable[[BlackboardEvent], Awaitable[None]]]] = {}

        # Event History for auditing / final report
        self.history: List[BlackboardEvent] = []

        # Lock for state updates
        self._lock = asyncio.Lock()

    async def update_state(self, key: str, value: Any, agent_name: str):
        """Atomically update the shared state dictionary."""
        async with self._lock:
            self.state[key] = value
            logger.debug(f"[Blackboard] State '{key}' updated by {agent_name}.")

    async def get_state(self, key: str) -> Any:
        """Retrieve a value from the shared state."""
        async with self._lock:
            return self.state.get(key)

    def subscribe(self, topic: str, callback: Callable[[BlackboardEvent], Awaitable[None]]):
        """Register an async callback for a specific event topic."""
        if topic not in self.subscribers:
            self.subscribers[topic] = []
        self.subscribers[topic].append(callback)
        logger.debug(f"[Blackboard] Registered callback for topic '{topic}'.")

    async def publish(self, topic: str, source_agent: str, data: Dict[str, Any]):
        """Publish an event to all subscribers of the given topic."""
        event = BlackboardEvent(topic, source_agent, data)
        self.history.append(event)

        logger.info(f"[Blackboard] Event published: {topic} (from {source_agent})")

        callbacks = self.subscribers.get(topic, [])
        if callbacks:
            # Execute callbacks concurrently
            tasks = [callback(event) for callback in callbacks]
            # Use return_exceptions=True to prevent one bad subscriber from crashing the publisher
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, res in enumerate(results):
                if isinstance(res, Exception):
                    logger.error(f"[Blackboard] Error in subscriber callback for '{topic}': {res}")
        else:
            logger.debug(f"[Blackboard] No subscribers for topic '{topic}'.")

    async def wait_for_event(self, topic: str, timeout: float = None) -> BlackboardEvent:
        """
        Utility for an agent to wait for a specific event to occur.
        Creates a temporary one-time subscriber.
        """
        future = asyncio.Future()

        async def _one_time_callback(event: BlackboardEvent):
            if not future.done():
                future.set_result(event)

        # We need a way to unsubscribe, but for simplicity, we append it
        # and rely on the fact that `future.set_result` will only work once.
        self.subscribe(topic, _one_time_callback)

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            # Attempt to clean up the callback if possible (requires removing from list)
            if topic in self.subscribers:
                try:
                    self.subscribers[topic].remove(_one_time_callback)
                except ValueError:
                    pass
            raise
