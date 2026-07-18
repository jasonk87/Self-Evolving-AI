import sys
from unittest import mock

# Mock prompt_toolkit output to avoid NoConsoleScreenBufferError in test environment
from prompt_toolkit.output import DummyOutput

with mock.patch('prompt_toolkit.output.defaults.create_output', return_value=DummyOutput()):
    # Add project root to sys.path
    import os
    sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

    from ai_assistant.communication import cli
    from ai_assistant.core.task_manager import TaskManager
    from ai_assistant.core.notification_manager import NotificationManager
    from ai_assistant.core.orchestrator import DynamicOrchestrator

    def main():
        notification_manager = NotificationManager()
        task_manager = TaskManager(notification_manager=notification_manager)
        orchestrator = mock.Mock(spec=DynamicOrchestrator)
        orchestrator.get_blocked_tools.return_value = {}
        
        with mock.patch('prompt_toolkit.output.defaults.create_output', return_value=DummyOutput()):
            tui = cli.WeeboTUI(task_manager, notification_manager, orchestrator)
        
        # Mock focus to active_tasks
        tui.layout.has_focus = lambda pane: pane == tui.panes["active_tasks"]
        tui.selected_indices["active_tasks"] = 2
        
        print("--- Testing update_dashboard_displays() with new cursor logic ---")
        tui.update_dashboard_displays()
        
        # Test cursor alignment logic manually on active_tasks
        focused_pane = tui.panes["active_tasks"]
        text = focused_pane.text
        sel_idx = tui.selected_indices["active_tasks"]
        lines = text.split('\n')
        pos = sum(len(line) + 1 for line in lines[:sel_idx])
        focused_pane.buffer.cursor_position = min(pos, len(text))
        
        print("Active tasks text start:", repr(text[:200]))
        print("Selected index:", sel_idx)
        print("Cursor position:", focused_pane.buffer.cursor_position)
        print("Character at cursor:", repr(text[focused_pane.buffer.cursor_position : focused_pane.buffer.cursor_position + 20]))
        
        # Non-focused pane
        unfocused_pane = tui.panes["background_agents"]
        unfocused_pane.buffer.cursor_position = 0
        print("Unfocused pane cursor position:", unfocused_pane.buffer.cursor_position)

    if __name__ == "__main__":
        main()
