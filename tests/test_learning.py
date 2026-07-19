import unittest
from unittest import mock # Ensure mock is imported
import os
import datetime
import tempfile
import json
import uuid # Added for generating entry_ids
from typing import List, Dict, Any, Optional

# Attempt to import from the ai_assistant package.
try:
    from ai_assistant.learning.learning import (
        DISMISSED_AS_FIXED_STATUS,
        LearningAgent,
        ActionableInsight,
        InsightType,
        record_insight_rejection,
    )
    from ai_assistant.core.reflection import ReflectionLogEntry
except ImportError: # pragma: no cover
    import sys
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ai_assistant.learning.learning import (
        DISMISSED_AS_FIXED_STATUS,
        LearningAgent,
        ActionableInsight,
        InsightType,
        record_insight_rejection,
    )
    from ai_assistant.core.reflection import ReflectionLogEntry


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


class TestLearningAgent(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.temp_insights_file = tempfile.NamedTemporaryFile(delete=False, mode='w+', suffix='.json')
        self.temp_insights_filepath = self.temp_insights_file.name
        self.temp_insights_file.close()
        # self.agent is no longer created here to allow per-test mocking of ActionExecutor

    def tearDown(self):
        if os.path.exists(self.temp_insights_filepath):
            os.remove(self.temp_insights_filepath)
        state_path = f"{os.path.splitext(self.temp_insights_filepath)[0]}.conversation_analysis_state.json"
        if os.path.exists(state_path):
            os.remove(state_path)

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

    async def test_process_reflection_entry_generates_insight(self):
        with mock.patch('ai_assistant.learning.learning.ActionExecutor'): # Mock ActionExecutor
            agent = LearningAgent(insights_filepath=self.temp_insights_filepath)

        failed_plan = [{"tool_name": "broken_tool", "args": ["a"], "kwargs": {}}]
        mock_entry_failure = self._create_mock_reflection_entry(
            goal="test failure", status="FAILURE", error_type="TestError",
            error_message="Something broke", plan=failed_plan, results=[Exception("TestError")]
        )
        insight = await agent.process_reflection_entry(mock_entry_failure)

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

    def test_add_insight_merges_duplicate_tool_bug_guesses_from_same_evidence(self):
        with mock.patch('ai_assistant.learning.learning.ActionExecutor'):
            agent = LearningAgent(insights_filepath=self.temp_insights_filepath)

        evidence = (
            "ASSISTANT: listed the agi project with terminal access. "
            "ASSISTANT: background task failed because project agi was not found."
        )
        first = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description=(
                "ISSUE DETECTED: The AI had inconsistent agi project context. "
                f"(Evidence: {evidence})"
            ),
            source_reflection_entry_ids=[],
            related_tool_name="Internal Agent Orchestration / Context Management",
        )
        second = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description=(
                "ISSUE DETECTED: The file reader may be involved in the agi failure. "
                "(Evidence: ASSISTANT: agi project files included app.py and "
                "autogen_core_v1.py. ASSISTANT: project agi was not found when "
                "the background agent tried to read those files.)"
            ),
            source_reflection_entry_ids=[],
            related_tool_name="read_text_from_file",
        )

        self.assertTrue(agent.add_insight(first))
        self.assertFalse(agent.add_insight(second))
        self.assertEqual(len(agent.insights), 1)
        self.assertEqual(
            agent.insights[0].metadata["candidate_related_tool_names"],
            [
                "Internal Agent Orchestration / Context Management",
                "read_text_from_file",
            ],
        )
        self.assertEqual(agent.insights[0].metadata["merged_duplicate_count"], 1)

    def test_add_insight_rejects_demo_tool_bug_evidence(self):
        with mock.patch('ai_assistant.learning.learning.ActionExecutor'):
            agent = LearningAgent(insights_filepath=self.temp_insights_filepath)

        junk = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description=(
                "ISSUE DETECTED: The AI made a demonstrably false statement about "
                "the sky's color. (Evidence: apparently, the sky is currently "
                "both blue and green (quite the cosmic mystery!))"
            ),
            source_reflection_entry_ids=[],
            related_tool_name="reasoning",
        )

        self.assertFalse(agent.add_insight(junk))
        self.assertEqual(agent.insights, [])

    def test_already_fixed_rejection_suppresses_reworded_conversation_bug(self):
        with mock.patch('ai_assistant.learning.learning.ActionExecutor'):
            agent = LearningAgent(insights_filepath=self.temp_insights_filepath)

        rejected = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description=(
                "ISSUE DETECTED: The 'recall_facts' tool crashed after calling "
                ".lower() on dictionary objects and was quarantined."
            ),
            source_reflection_entry_ids=[],
            metadata={"source": "conversational_analysis", "session_id": "session-1"},
        )
        self.assertTrue(agent.add_insight(rejected))
        self.assertEqual(
            record_insight_rejection(rejected, "This was fixed already"),
            DISMISSED_AS_FIXED_STATUS,
        )

        repeated = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description=(
                "ISSUE DETECTED: recall_facts calls lower on memory records that are "
                "dictionaries, causing repeated failures and quarantine."
            ),
            source_reflection_entry_ids=[],
            metadata={"source": "conversational_analysis", "session_id": "session-1"},
        )

        self.assertFalse(agent.add_insight(repeated))
        self.assertEqual(len(agent.insights), 1)
        self.assertEqual(agent.insights[0].status, DISMISSED_AS_FIXED_STATUS)
        self.assertEqual(agent.insights[0].metadata["suppressed_duplicate_count"], 1)
        self.assertEqual(agent.insights[0].metadata["rejection_feedback"], "This was fixed already")

    def test_real_post_dismissal_runtime_failure_can_reopen_issue(self):
        with mock.patch('ai_assistant.learning.learning.ActionExecutor'):
            agent = LearningAgent(insights_filepath=self.temp_insights_filepath)

        rejected = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description="ISSUE DETECTED: The 'recall_facts' tool called lower on a dictionary.",
            source_reflection_entry_ids=["old-runtime-event"],
        )
        self.assertTrue(agent.add_insight(rejected))
        record_insight_rejection(rejected, "already fixed")
        rejected.metadata["dismissed_at"] = "2026-07-18T10:00:00+00:00"

        regression = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description="ISSUE DETECTED: recall_facts crashed because lower received dictionary records.",
            source_reflection_entry_ids=["new-runtime-event"],
            metadata={
                "source": "reflection_log",
                "failure_observed_at": "2026-07-18T10:05:00+00:00",
            },
        )

        self.assertTrue(agent.add_insight(regression))
        self.assertEqual(len(agent.insights), 2)

    def test_superseded_conversation_bug_stays_suppressed_but_runtime_regression_reopens(self):
        with mock.patch('ai_assistant.learning.learning.ActionExecutor'):
            agent = LearningAgent(insights_filepath=self.temp_insights_filepath)

        superseded = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description="ISSUE DETECTED: DeepSeek timeout was classified as a dream_mode_runner tool failure.",
            source_reflection_entry_ids=[],
            status="SUPERSEDED_EXTERNAL_UPDATE",
            metadata={"source": "conversational_analysis"},
        )
        self.assertTrue(agent.add_insight(superseded))

        rediscovered = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description="ISSUE DETECTED: A DeepSeek timeout triggered a repair approval for dream mode runner.",
            source_reflection_entry_ids=[],
            metadata={"source": "conversational_analysis"},
        )
        self.assertFalse(agent.add_insight(rediscovered))
        self.assertEqual(len(agent.insights), 1)

        runtime_regression = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description="ISSUE DETECTED: DeepSeek timeout was classified as a dream_mode_runner tool failure.",
            source_reflection_entry_ids=["runtime-regression"],
            metadata={"source": "reflection_log"},
        )
        self.assertTrue(agent.add_insight(runtime_regression))
        self.assertEqual(len(agent.insights), 2)

    async def test_conversation_scan_checkpoints_unchanged_transcript(self):
        with mock.patch('ai_assistant.learning.learning.ActionExecutor'):
            agent = LearningAgent(insights_filepath=self.temp_insights_filepath)

        session = {
            "id": "session-checkpoint",
            "history": [
                {"role": "user", "content": "The weather output was wrong."},
                {"role": "assistant", "content": "I will investigate."},
            ],
        }
        agent.chat_manager = mock.Mock()
        agent.chat_manager.list_sessions.return_value = [{"id": session["id"]}]
        agent.chat_manager.get_session.side_effect = lambda _session_id: session

        class FakeAnalyst:
            def __init__(self):
                self.calls = []
                self.last_analysis_succeeded = True

            async def analyze_session_transcript(self, session_data, focus_start_index=0):
                self.calls.append((list(session_data["history"]), focus_start_index))
                return []

        analyst = FakeAnalyst()
        agent.conversational_analyst = analyst

        self.assertEqual(await agent.scan_recent_conversations(), 0)
        self.assertEqual(await agent.scan_recent_conversations(), 0)
        self.assertEqual(len(analyst.calls), 1)

        session["history"].append({"role": "user", "content": "It is fixed now."})
        self.assertEqual(await agent.scan_recent_conversations(), 0)
        self.assertEqual(len(analyst.calls), 2)
        self.assertEqual(analyst.calls[1][1], 2)

    async def test_conversation_scan_bootstraps_checkpoint_from_newer_existing_analysis(self):
        with mock.patch('ai_assistant.learning.learning.ActionExecutor'):
            agent = LearningAgent(insights_filepath=self.temp_insights_filepath)

        existing = ActionableInsight(
            type=InsightType.USER_PREFERENCE_LEARNED,
            description="User prefers readable output.",
            source_reflection_entry_ids=[],
            creation_timestamp="2026-07-18T10:05:00+00:00",
            metadata={"source": "conversational_analysis", "session_id": "session-migrate"},
        )
        self.assertTrue(agent.add_insight(existing))
        session = {
            "id": "session-migrate",
            "updated_at": "2026-07-18T10:00:00+00:00",
            "history": [{"role": "user", "content": "Make output readable."}],
        }
        agent.chat_manager = mock.Mock()
        agent.chat_manager.list_sessions.return_value = [{"id": session["id"]}]
        agent.chat_manager.get_session.return_value = session
        agent.conversational_analyst = mock.Mock()
        agent.conversational_analyst.analyze_session_transcript = mock.AsyncMock(
            side_effect=AssertionError("migrated transcript must not be analyzed again")
        )

        self.assertEqual(await agent.scan_recent_conversations(), 0)
        checkpoint = agent._conversation_analysis_state["sessions"][session["id"]]
        self.assertEqual(checkpoint["message_count"], 1)
        self.assertTrue(checkpoint["migrated_from_existing_insights"])
        agent.conversational_analyst.analyze_session_transcript.assert_not_awaited()

    def test_add_insight_does_not_merge_new_failure_into_completed_tool_bug(self):
        with mock.patch('ai_assistant.learning.learning.ActionExecutor'):
            agent = LearningAgent(insights_filepath=self.temp_insights_filepath)

        completed = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description=(
                "ISSUE DETECTED: The agi desktop project was listed through terminal "
                "but later reported as not found."
            ),
            source_reflection_entry_ids=["old_entry"],
            related_tool_name="read_text_from_file",
            status="ACTION_SUCCESSFUL",
        )
        new_failure = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description=(
                "ISSUE DETECTED: The agi project context failed again because the "
                "background agent reported project agi was not found after terminal access."
            ),
            source_reflection_entry_ids=["new_entry"],
            related_tool_name="Internal Agent Orchestration / Context Management",
            status="NEW",
        )

        self.assertTrue(agent.add_insight(completed))
        self.assertTrue(agent.add_insight(new_failure))
        self.assertEqual(len(agent.insights), 2)
        self.assertNotIn("merged_duplicate_count", agent.insights[0].metadata)

    def test_add_insight_merges_duplicate_into_queued_tool_bug(self):
        with mock.patch('ai_assistant.learning.learning.ActionExecutor'):
            agent = LearningAgent(insights_filepath=self.temp_insights_filepath)

        queued = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description=(
                "ISSUE DETECTED: The agi desktop project was listed through terminal "
                "but later reported as not found."
            ),
            source_reflection_entry_ids=["queued_entry"],
            related_tool_name="read_text_from_file",
            status="APPROVED_QUEUED",
        )
        duplicate = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description=(
                "ISSUE DETECTED: The agi project context failed again because the "
                "background agent reported project agi was not found after terminal access."
            ),
            source_reflection_entry_ids=["dup_entry"],
            related_tool_name="Internal Agent Orchestration / Context Management",
            status="NEW",
        )

        self.assertTrue(agent.add_insight(queued))
        self.assertFalse(agent.add_insight(duplicate))
        self.assertEqual(len(agent.insights), 1)
        self.assertEqual(agent.insights[0].metadata["merged_duplicate_count"], 1)

    def test_add_insight_merges_startup_recovery_digest_variants(self):
        with mock.patch('ai_assistant.learning.learning.ActionExecutor'):
            agent = LearningAgent(insights_filepath=self.temp_insights_filepath)

        first = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description=(
                "ISSUE DETECTED: Startup recovery digest messages were injected "
                "into the user-facing conversation. (Evidence: Startup recovery "
                "digest marked tasks as FAILED_INTERRUPTED.)"
            ),
            source_reflection_entry_ids=[],
        )
        second = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description=(
                "ISSUE DETECTED: The AI repeatedly issues internal FAILED_INTERRUPTED "
                "shutdown recovery text into chat."
            ),
            source_reflection_entry_ids=[],
        )

        self.assertTrue(agent.add_insight(first))
        self.assertFalse(agent.add_insight(second))
        self.assertEqual(len(agent.insights), 1)
        self.assertEqual(
            agent.insights[0].metadata["insight_fingerprint"],
            "TOOL_BUG_SUSPECTED:theme:startup-recovery-digest-chat-leak",
        )
        self.assertEqual(agent.insights[0].metadata["merged_duplicate_count"], 1)

    def test_add_insight_merges_maximum_cycles_variants(self):
        with mock.patch('ai_assistant.learning.learning.ActionExecutor'):
            agent = LearningAgent(insights_filepath=self.temp_insights_filepath)

        first = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description=(
                "ISSUE DETECTED: A location lookup failed. "
                "(Evidence: Reason: Maximum cycles reached.)"
            ),
            source_reflection_entry_ids=[],
            related_tool_name="Task Execution",
        )
        second = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description=(
                "ISSUE DETECTED: Task execution and error reporting produced "
                "redundant output after Maximum cycles reached."
            ),
            source_reflection_entry_ids=[],
            related_tool_name="Task Execution and Error Reporting System",
        )

        self.assertTrue(agent.add_insight(first))
        self.assertFalse(agent.add_insight(second))
        self.assertEqual(len(agent.insights), 1)
        self.assertEqual(
            agent.insights[0].metadata["insight_fingerprint"],
            "TOOL_BUG_SUSPECTED:theme:maximum-cycles-reached",
        )
        self.assertEqual(agent.insights[0].metadata["merged_duplicate_count"], 1)

    async def test_hypothetical_scenario_waits_for_manual_review(self):
        agent = LearningAgent(insights_filepath=self.temp_insights_filepath)
        agent.action_executor = mock.AsyncMock()
        insight = ActionableInsight(
            type=InsightType.HYPOTHETICAL_SCENARIO,
            description="Synthetic null-byte scenario failed for build_project.",
            source_reflection_entry_ids=[],
            related_tool_name="build_project",
            status="NEW",
            metadata={"source": "dream_mode"},
        )
        agent.insights.append(insight)

        result = await agent.review_and_propose_next_action()

        self.assertIsNotNone(result)
        proposed_action, executed = result
        self.assertEqual(proposed_action["action_type"], "REVIEW_MANUALLY")
        self.assertFalse(executed)
        self.assertEqual(insight.status, "PENDING_MANUAL_REVIEW")
        self.assertTrue(insight.metadata["synthetic_evidence"])
        agent.action_executor.execute_action.assert_not_awaited()

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

    async def test_approved_preference_is_consumed_by_general_learning(self):
        agent = LearningAgent(insights_filepath=self.temp_insights_filepath)
        agent.action_executor = mock.AsyncMock()
        agent.action_executor.execute_action.return_value = True
        insight = ActionableInsight(
            type=InsightType.USER_PREFERENCE_LEARNED,
            description="User prefers concise progress updates.",
            source_reflection_entry_ids=[],
            status="APPROVED_BY_USER",
        )
        agent.insights.append(insight)

        result = await agent.review_and_propose_next_action()

        self.assertIsNotNone(result)
        action, executed = result
        self.assertEqual(action["action_type"], "ADD_PLANNING_HEURISTIC")
        self.assertTrue(executed)
        self.assertEqual(insight.status, "ACTION_SUCCESSFUL")

    async def test_approved_tool_bug_is_applied_by_self_healing(self):
        agent = LearningAgent(insights_filepath=self.temp_insights_filepath)
        insight = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description="Confirmed parser failure.",
            source_reflection_entry_ids=[],
            related_tool_name="test_tool",
            status="APPROVED_BY_USER",
        )
        agent.insights.append(insight)
        agent.execute_self_healing_for_insight = mock.AsyncMock(return_value=True)

        processed = await agent.process_self_healing_insights()

        self.assertEqual(processed, 1)
        agent.execute_self_healing_for_insight.assert_awaited_once_with(
            insight,
            apply_immediately=True,
        )

if __name__ == '__main__': # pragma: no cover
    unittest.main()
