import sys
import os
import asyncio
from unittest.mock import MagicMock, AsyncMock

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))

async def verify_session_summary():
    print("Verifying Session Summary Logic...")
    
    # Mock modules
    sys.modules['ai_assistant.tools'] = MagicMock()
    sys.modules['ai_assistant.tools.project_management_tools'] = MagicMock()
    sys.modules['ai_assistant.tools.tool_system'] = MagicMock()
    sys.modules['ai_assistant.llm_interface.gemini_client'] = MagicMock()
    # Mock specific tool_system_instance import potentially
    ts_mock = MagicMock()
    sys.modules['ai_assistant.tools.tool_system'].tool_system_instance = ts_mock
    
    # Mock invoke_gemini_model_async to return a fake summary
    execute_mock = AsyncMock(return_value="This is a summary of the session.")
    sys.modules['ai_assistant.llm_interface.gemini_client'].invoke_gemini_model_async = execute_mock
    
    try:
        from ai_assistant.core.orchestrator import DynamicOrchestrator
        
        # Instantiate Orchestrator with mocks
        orch = DynamicOrchestrator(
            planner=MagicMock(), 
            executor=MagicMock(),
            learning_agent=MagicMock(),
            action_executor=MagicMock(),
            memory_manager=MagicMock()
        )
        orch.task_manager = MagicMock() # Needs this to trigger log
        
        # Setup Memory Manager Mock
        # Scenario 1: No existing episode for session
        orch.memory_manager.get_all_episodes.return_value = []
        
        print("Testing Creation of New Session Episode...")
        await orch._update_session_summary("test_session_123", [{"role": "user", "content": "hi"}])
        
        orch.memory_manager.add_episode.assert_called()
        print("PASS: add_episode called for new session.")
        
        # Scenario 2: Existing episode
        orch.memory_manager.get_all_episodes.return_value = [{"episode_id": "ep_1", "session_id": "test_session_123"}]
        
        print("Testing Update of Existing Session Episode...")
        await orch._update_session_summary("test_session_123", [{"role": "user", "content": "hi"}])
        
        orch.memory_manager.update_episode.assert_called_with(
            episode_id="ep_1",
            summary="This is a summary of the session.",
            title=None
        )
        print("PASS: update_episode called for existing session.")
            
    except Exception as e:
        print(f"Error during verification: {e}")
        import traceback
        traceback.print_exc()
        return False

    print("Verification complete.")
    return True

if __name__ == "__main__":
    asyncio.run(verify_session_summary())
