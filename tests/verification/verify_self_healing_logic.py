
import asyncio
from unittest.mock import MagicMock, patch
from ai_assistant.learning.learning import LearningAgent
from ai_assistant.core.reflection import ActionableInsight, InsightType
from ai_assistant.execution.action_executor import ActionExecutor

# Mock dependencies
class MockDB:
    def __init__(self):
        self.insights = []
    def load_insights(self): return self.insights
    def save_insights(self, insights): self.insights = insights

async def test_caller_error_detection():
    print("\n--- Testing Caller Error Detection in LearningAgent ---")
    
    agent = LearningAgent()
    # Mock specific methods to avoid full initialization
    agent._load_insights = MagicMock(return_value=[])
    agent._save_insights = MagicMock()
    
    # Create a simulated insight representing a Caller Error
    insight = ActionableInsight(
        insight_id="test_insight_1",
        type=InsightType.TOOL_BUG_SUSPECTED,
        description="TypeError: execute_safe_terminal_command() takes 1 positional argument but 2 were given",
        priority=1, # Use int instead of Enum
        related_tool_name="execute_safe_terminal_command",
        source_reflection_entry_ids=[],
        metadata={"module_path": "ai_assistant/tools/generic.py"}
    )
    
    # We need to inject this insight into a list and pass it to logic, 
    # but review_and_propose_next_action isn't easily unit-testable without mocking internal lists.
    # However, looking at the code, we can test the logic if we can control the 'selected_insight'.
    # The method selects the first insight from the list passed to it or internal list.
    # Let's mock the internal processing loop or copy the logic snippet for verification if imports are too heavy.
    # Actually, let's try to run the actual method if we can mock the inputs.
    
    # We'll use a slightly invasive technique: we will modify the agent's actionable_new_insights list manually
    # effectively bypassing the loading logic.
    
    # The method signature: review_and_propose_next_action(self, recent_reflection_entries)
    # It filters internal insights. We need to populate self.insights first? 
    # No, it processes `self.insights` that are NEW.
    
    agent.insights = [insight]
    
    # Mocking external calls
    with patch("ai_assistant.learning.learning.get_tool", return_value={"module_path": "mock/path"}):
        action = await agent.review_and_propose_next_action()
    
    if action and action[0]["action_type"] == "ADD_PLANNING_HEURISTIC":
        print("[PASS] Correctly identified Caller Error and proposed ADD_PLANNING_HEURISTIC.")
        print(f"Details: {action[0]['details']}")
    else:
        print(f"[FAIL] Expected ADD_PLANNING_HEURISTIC. Got: {action[0]['action_type'] if action else 'None'}")
        print(f"Details: {action[0].get('details') if action else ''}")

async def test_noop_prevention():
    print("\n--- Testing No-Op Code Change Prevention in ActionExecutor ---")
    
    # Mock dependencies
    mock_learning_agent = MagicMock()
    mock_task_manager = MagicMock()
    executor = ActionExecutor(learning_agent=mock_learning_agent, task_manager=mock_task_manager)
    
    # Mock Code Service and Modification Service
    executor.code_service = MagicMock()
    executor.code_service.llm_provider = MagicMock() # For council debate mock if needed
    
    module_path = "mock_module.py"
    function_name = "mock_function"
    original_code = "def mock_function():\n    return 'original'"
    
    # Proposed code is identical (with extra whitespace to test normalization)
    proposed_code = "def mock_function():\n    return 'original'  " 
    
    with patch("ai_assistant.core.self_modification.get_function_source_code", return_value=original_code), \
         patch("ai_assistant.core.self_modification.get_backup_function_source_code", return_value=original_code), \
         patch("ai_assistant.execution.action_executor.global_reflection_log") as _mock_log:
             
        success = await executor._apply_test_and_revert_code(
            module_path=module_path,
            function_name=function_name,
            code_to_apply=proposed_code,
            original_description="Attempting no-op change",
            source_insight_id="test_insight_2",
            action_task_id="task_123"
        )
        
        if success is False:
             # Check if it was false because of No-OP or something else
             # We look at the calls to logging
             # logic: if normalize match -> logs warning and returns False
             print("[PASS] _apply_test_and_revert_code returned False for identical code.")
             # Ideally check mock_log calls to ensure it was the right reason
        else:
             print("[FAIL] _apply_test_and_revert_code returned True (or didn't fail early) for identical code.")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(test_caller_error_detection())
    loop.run_until_complete(test_noop_prevention())
