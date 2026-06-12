import asyncio
import os
import sys
import logging
from unittest.mock import MagicMock, patch, AsyncMock
import traceback

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

try:
    from ai_assistant.learning.learning import LearningAgent
    from ai_assistant.core.reflection import ActionableInsight, InsightType
    from ai_assistant.execution.action_executor import ActionExecutor
except Exception as e:
    print(f"Import Error: {e}")
    traceback.print_exc()
    sys.exit(1)

async def test_user_approval_bypass_staging():
    print("\n--- Test 1: User Approval (Bypass Staging) ---")
    try:
        mock_task_manager = MagicMock()
        mock_notification_manager = MagicMock()
        agent = LearningAgent(task_manager=mock_task_manager, notification_manager=mock_notification_manager)
        
        # Mock ActionExecutor.execute_action
        agent.action_executor.execute_action = AsyncMock(return_value=True)

        insight = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description="Test bug",
            source_reflection_entry_ids=["mock_entry_id"],
            related_tool_name="test_tool",
            metadata={"module_path": "tools.test", "function_name": "test_func"}
        )
        
        with patch("ai_assistant.learning.learning.get_tool", return_value={"module_path": "tools.test", "function_name": "test_func"}):
             await agent.execute_self_healing_for_insight(insight, apply_immediately=True)

        call_args = agent.action_executor.execute_action.call_args[0][0]
        details = call_args["details"]
        print(f"Call Details: {details}")
        
        actual_staging = details.get("staging_mode")
        
        if actual_staging is False:
             print("SUCCESS: staging_mode is False as expected.")
        else:
             print(f"FAILURE: staging_mode is {actual_staging}, expected False.")
             raise Exception("staging_mode check failed")

    except Exception as e:
        print(f"Test 1 Failed: {e}")
        traceback.print_exc()
        raise

async def test_council_rejection_learning():
    print("\n--- Test 2: Council Rejection Learning ---")

    try:
        # In-memory store for verification
        saved_facts_store = []

        def fake_load_facts(filepath=None):
            # Return a COPY to simulate reading from disk
            return list(saved_facts_store)
            
        def fake_save_facts(facts, filepath=None):
            # Update our store with the facts passed to save
            saved_facts_store.clear()
            saved_facts_store.extend(facts)
            return True

        with patch("ai_assistant.memory.persistent_memory.load_learned_facts", side_effect=fake_load_facts) as mock_load, \
             patch("ai_assistant.memory.persistent_memory.save_learned_facts", side_effect=fake_save_facts) as mock_save:
            
            agent = MagicMock()
            executor = ActionExecutor(learning_agent=agent)
            executor.code_service = MagicMock()
            executor.global_reflection_log = MagicMock()
            
            mock_coordinator = MagicMock()
            mock_coordinator.execute_council_debate = AsyncMock(return_value=(False, "Test Rejection Reason: Unsafe Code"))
            
            with patch("ai_assistant.core.critical_reviewer.CriticalReviewCoordinator", return_value=mock_coordinator):
                with patch("ai_assistant.execution.action_executor.self_modification") as mock_self_mod:
                    mock_self_mod.get_function_source_code.return_value = "def foo(): pass"
                    mock_self_mod.get_backup_function_source_code.return_value = "def foo(): pass" 
                    
                    with patch("ai_assistant.core.reviewer.ReviewerAgent"):
                        await executor._apply_test_and_revert_code(
                            module_path="tools.test",
                            function_name="test_func",
                            code_to_apply="def foo(): pass # modified",
                            original_description="Fix bug",
                            source_insight_id="insight_123",
                            action_task_id="task_1"
                        )
            
            if mock_save.called:
                print("SUCCESS: save_learned_facts was called.")
                
                # Check saved_facts_store
                facts_list = saved_facts_store
                print(f"Saved Facts Store: {[f['text'] for f in facts_list]}") 
                
                found_reason = any("Test Rejection Reason: Unsafe Code" in f["text"] for f in facts_list)
                found_council = any("Council rejected modification" in f["text"] for f in facts_list)
                
                if found_reason and found_council:
                    print("SUCCESS: Fact content verification passed.")
                else:
                    print(f"FAILURE: Fact content mismatch.")
                    raise Exception("Fact verification failed")
            else:
                print("FAILURE: save_learned_facts was NOT called.")
                raise Exception("save_learned_facts not called")

    except Exception as e:
        print(f"Test 2 Failed: {e}")
        # traceback.print_exc() 
        raise

async def main():
    try:
        await test_user_approval_bypass_staging()
        await test_council_rejection_learning()
        print("\nALL TESTS PASSED")
    except Exception as e:
        print("\nTESTS FAILED")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
