import unittest
from unittest import mock # Ensure mock is imported
import asyncio
import os
import datetime
import tempfile
import json
import uuid # Added for generating entry_ids
from typing import List, Dict, Any, Optional
from dataclasses import field # Ensure field is imported for dataclasses

# Attempt to import from the ai_assistant package.
try:
    from ai_assistant.learning.learning import LearningAgent, ActionableInsight, InsightType
    from ai_assistant.core.reflection import ReflectionLogEntry
    from ai_assistant.execution.action_executor import ActionExecutor # Needed for patching target
except ImportError: # pragma: no cover
    import sys
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ai_assistant.learning.learning import LearningAgent, ActionableInsight, InsightType
    from ai_assistant.core.reflection import ReflectionLogEntry
    from ai_assistant.execution.action_executor import ActionExecutor


class TestActionableInsight(unittest.TestCase):

    def test_insight_creation_default_id(self):
        timestamp_now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        insight = ActionableInsight(
            type=InsightType.KNOWLEDGE_GAP_IDENTIFIED,
            description="Test description",
            source_reflection_entry_ids=["entry1"],
            creation_timestamp=timestamp_now_iso
        )
        self.assertIsNotNone(insight.insight_id)
        self.assertTrue(InsightType.KNOWLEDGE_GAP_IDENTIFIED.name in insight.insight_id)
        self.assertEqual(insight.status, "NEW")
        self.assertEqual(insight.priority, 5)

    def test_insight_creation_with_id(self):
        insight = ActionableInsight(
            insight_id="custom_id_123",
            type=InsightType.TOOL_BUG_SUSPECTED,
            description="Tool bug",
            source_reflection_entry_ids=["entry2"]
        )
        self.assertEqual(insight.insight_id, "custom_id_123")


class TestLearningAgent(unittest.TestCase):

    def setUp(self):
        self.temp_insights_file = tempfile.NamedTemporaryFile(delete=False, mode='w+', suffix='.json')
        self.temp_insights_filepath = self.temp_insights_file.name
        self.temp_insights_file.close()
        # self.agent is no longer created here to allow per-test mocking of ActionExecutor

    def tearDown(self):
        if os.path.exists(self.temp_insights_filepath):
            os.remove(self.temp_insights_filepath)

    def _create_mock_reflection_entry(
        self,
        goal: str,
        status: str,
        entry_id: Optional[str] = None, # Added entry_id for explicit setting if needed
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        plan: Optional[List[Dict[str, Any]]] = None,
        results: Optional[List[Any]] = None,
        notes: Optional[str] = None
    ) -> ReflectionLogEntry:
        # ReflectionLogEntry's default_factory for entry_id will handle it if None
        return ReflectionLogEntry(
            entry_id=entry_id if entry_id else str(uuid.uuid4()), # Ensure it has an ID
            goal_description=goal,
            status=status,
            plan=plan if plan is not None else [],
            execution_results=results if results is not None else [],
            error_type=error_type,
            error_message=error_message,
            notes=notes,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )

    def test_agent_initialization_empty_file(self):
        # Patch ActionExecutor during LearningAgent instantiation for this test
        with mock.patch('ai_assistant.learning.learning.ActionExecutor') as MockedActionExecutor:
            agent = LearningAgent(insights_filepath=self.temp_insights_filepath)
            self.assertEqual(len(agent.insights), 0)
            MockedActionExecutor.assert_called_once()


    def test_agent_initialization_with_existing_insights(self):
        timestamp_now = datetime.datetime.now(datetime.timezone.utc)
        insights_data = [
            ActionableInsight(
                insight_id="id1", type=InsightType.KNOWLEDGE_GAP_IDENTIFIED,
                description="desc1", source_reflection_entry_ids=["src1"],
                creation_timestamp=timestamp_now.isoformat()
            ).__dict__,
            ActionableInsight(
                insight_id="id2", type=InsightType.TOOL_BUG_SUSPECTED,
                description="desc2", source_reflection_entry_ids=["src2"],
                related_tool_name="tool_A",
                creation_timestamp=(timestamp_now + datetime.timedelta(seconds=1)).isoformat()
            ).__dict__
        ]
        insights_data[0]['type'] = InsightType.KNOWLEDGE_GAP_IDENTIFIED.name
        insights_data[1]['type'] = InsightType.TOOL_BUG_SUSPECTED.name

        with open(self.temp_insights_filepath, 'w') as f:
            json.dump(insights_data, f)

        with mock.patch('ai_assistant.learning.learning.ActionExecutor'): # Mock ActionExecutor
            agent = LearningAgent(insights_filepath=self.temp_insights_filepath)
            self.assertEqual(len(agent.insights), 2)
            self.assertEqual(agent.insights[0].insight_id, "id1")
            self.assertEqual(agent.insights[1].type, InsightType.TOOL_BUG_SUSPECTED)

    def test_process_reflection_entry_generates_insight(self):
        with mock.patch('ai_assistant.learning.learning.ActionExecutor'): # Mock ActionExecutor
            agent = LearningAgent(insights_filepath=self.temp_insights_filepath)

        failed_plan = [{"tool_name": "broken_tool", "args": ["a"], "kwargs": {}}]
        mock_entry_failure = self._create_mock_reflection_entry(
            goal="test failure", status="FAILURE", error_type="TestError",
            error_message="Something broke", plan=failed_plan, results=[Exception("TestError")]
        )
        insight = agent.process_reflection_entry(mock_entry_failure)

        self.assertIsNotNone(insight)
        self.assertEqual(len(agent.insights), 1)
        if insight:
            self.assertEqual(insight.type, InsightType.TOOL_BUG_SUSPECTED)
            self.assertEqual(insight.related_tool_name, "broken_tool")
            self.assertEqual(insight.source_reflection_entry_ids[0], mock_entry_failure.entry_id) # Verify entry_id
            self.assertEqual(insight.metadata.get("original_reflection_entry_ref_id"), mock_entry_failure.entry_id) # Verify metadata

            self.assertTrue(os.path.exists(self.temp_insights_filepath))
            with open(self.temp_insights_filepath, 'r') as f:
                saved_data = json.load(f)
                self.assertEqual(len(saved_data), 1)
                self.assertEqual(saved_data[0]['insight_id'], insight.insight_id)

    async def test_review_and_propose_next_action_selects_highest_priority(self):
        # Instantiate agent here to allow easier mocking of its action_executor
        agent = LearningAgent(insights_filepath=self.temp_insights_filepath)
        agent.action_executor = mock.AsyncMock() # Replace with an AsyncMock instance

        ts_now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Insight for KNOWLEDGE_GAP_IDENTIFIED (priority 1)
        knowledge_insight_id = str(uuid.uuid4())
        high_priority_insight = ActionableInsight(
            insight_id="high_prio_insight",
            type=InsightType.KNOWLEDGE_GAP_IDENTIFIED,
            description="Urgent knowledge needed",
            source_reflection_entry_ids=[knowledge_insight_id],
            knowledge_to_learn="Learn X immediately",
            priority=1, status="NEW", creation_timestamp=ts_now_iso,
            metadata={'original_reflection_entry_ref_id': knowledge_insight_id}
        )

        # Insight for TOOL_BUG_SUSPECTED (priority 3)
        tool_mod_insight_id = str(uuid.uuid4())
        tool_mod_insight = ActionableInsight(
            insight_id="tool_mod_insight",
            type=InsightType.TOOL_BUG_SUSPECTED,
            description="Tool needs fix",
            source_reflection_entry_ids=[tool_mod_insight_id],
            related_tool_name="test_tool", priority=3, status="NEW",
            creation_timestamp=(datetime.datetime.fromisoformat(ts_now_iso) + datetime.timedelta(seconds=1)).isoformat(),
            metadata={
                'original_reflection_entry_ref_id': tool_mod_insight_id,
                'module_path': 'dummy.module',
                'function_name': 'dummy_func'
            }
        )
        agent.insights.extend([tool_mod_insight, high_priority_insight]) # Add higher prio last to test sorting
        agent._save_insights()

        # Test 1: Highest priority (knowledge_insight), execution success
        agent.action_executor.execute_action.return_value = True
        action_result_tuple_1 = await agent.review_and_propose_next_action()

        self.assertIsNotNone(action_result_tuple_1)
        if action_result_tuple_1:
            proposed_action_1, exec_success_1 = action_result_tuple_1
            self.assertTrue(exec_success_1)
            self.assertEqual(proposed_action_1["source_insight_id"], high_priority_insight.insight_id)
            self.assertEqual(proposed_action_1["action_type"], "ADD_LEARNED_FACT")
            found_insight_1 = next(i for i in agent.insights if i.insight_id == high_priority_insight.insight_id)
            self.assertEqual(found_insight_1.status, "ACTION_SUCCESSFUL")
        agent.action_executor.execute_action.assert_called_once()
        agent.action_executor.execute_action.reset_mock() # Reset for next call

        # Test 2: Next priority (tool_mod_insight), execution failure
        agent.action_executor.execute_action.return_value = False
        action_result_tuple_2 = await agent.review_and_propose_next_action()

        self.assertIsNotNone(action_result_tuple_2)
        if action_result_tuple_2:
            proposed_action_2, exec_success_2 = action_result_tuple_2
            self.assertFalse(exec_success_2)
            self.assertEqual(proposed_action_2["source_insight_id"], tool_mod_insight.insight_id)
            self.assertEqual(proposed_action_2["action_type"], "PROPOSE_TOOL_MODIFICATION")
            found_insight_2 = next(i for i in agent.insights if i.insight_id == tool_mod_insight.insight_id)
            self.assertEqual(found_insight_2.status, "ACTION_FAILED")
        agent.action_executor.execute_action.assert_called_once()
        agent.action_executor.execute_action.reset_mock()

        # Test 3: No more "NEW" insights
        action_result_tuple_3 = await agent.review_and_propose_next_action()
        self.assertIsNone(action_result_tuple_3)
        agent.action_executor.execute_action.assert_not_called() # Should not be called if no insights

if __name__ == '__main__': # pragma: no cover
    unittest.main()
