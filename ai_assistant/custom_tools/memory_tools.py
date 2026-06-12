
import logging
from typing import Optional

from ai_assistant.core.task_manager import TaskManager
from ai_assistant.core.notification_manager import NotificationManager

logger = logging.getLogger(__name__)

async def trigger_learning_scan(notification_manager: Optional[NotificationManager] = None) -> str:
    """
    Triggers an immediate scan of the recent conversation to learn important facts or user preferences.
    Use this when the user shares significant personal information, explicitly asks you to remember something,
    or provides detailed instructions that should be remembered permanently.
    
    This tool will force the AI to process 'Learned Facts' immediately.
    
    Returns:
        str: A summary of what was learned (if anything).
    """
    try:
        from ai_assistant.learning.learning import LearningAgent

        # Instantiate a temporary LearningAgent for this operation
        # We pass a minimal NotificationManager if none provided, though usually it is.
        nm = notification_manager or NotificationManager()
        tm = TaskManager(notification_manager=nm)
        
        agent = LearningAgent(task_manager=tm, notification_manager=nm)
        
        logger.info("trigger_learning_scan: Starting manual scan...")
        
        # 1. Scan
        new_insights_count = await agent.scan_recent_conversations()
        
        if new_insights_count == 0:
            return "Scan complete. No new insights found in recent conversation."
            
        # 2. Process Facts
        processed_facts = await agent.process_learned_facts_immediately()
        
        if processed_facts > 0:
            return f"Success! I have learned and saved {processed_facts} new permanent facts from our conversation."
        else:
            return f"Scan complete. Found {new_insights_count} insights, but they were likely not permanent facts or were already known."

    except Exception as e:
        logger.error(f"Error in trigger_learning_scan: {e}", exc_info=True)
        return f"Error executing learning scan: {str(e)}"
