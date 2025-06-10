# ai_assistant/custom_tools/suggestion_management_tools.py
from typing import Optional, Dict, Any, Literal
from ai_assistant.core.suggestion_manager import approve_suggestion, deny_suggestion, find_suggestion
from ai_assistant.core.notification_manager import NotificationManager # Made unconditional

def manage_suggestion_status(
    suggestion_id: str,
    action: Literal["approve", "deny"],
    reason: Optional[str] = None,
    notification_manager: Optional[NotificationManager] = None # Type hint updated
) -> Dict[str, Any]:
    """
    Approves or denies a specific suggestion by its ID.

    Args:
        suggestion_id: The ID of the suggestion to manage.
        action: The action to perform: "approve" or "deny".
        reason: Optional reason for the approval or denial.

    Returns:
        A dictionary with "status" (e.g., "success", "error") and "message".
    """
    if not suggestion_id or not action:
        return {"status": "error", "message": "suggestion_id and action are required."}

    action_lower = action.lower()
    if action_lower not in ["approve", "deny"]:
        return {"status": "error", "message": "Invalid action. Must be 'approve' or 'deny'."}

    sugg = find_suggestion(suggestion_id)
    if not sugg:
        return {"status": "error", "message": f"Suggestion ID '{suggestion_id}' not found."}

    if action_lower == "approve":
        if approve_suggestion(suggestion_id, reason, notification_manager=notification_manager): # Pass NM
            return {"status": "success", "message": f"Suggestion '{suggestion_id}' approved. Reason: {reason or 'N/A'}."}
        else: # pragma: no cover
            return {"status": "error", "message": f"Failed to approve suggestion '{suggestion_id}'. It might have been already actioned or an issue occurred."}
    elif action_lower == "deny":
        if deny_suggestion(suggestion_id, reason, notification_manager=notification_manager): # Pass NM
            return {"status": "success", "message": f"Suggestion '{suggestion_id}' denied. Reason: {reason or 'N/A'}."}
        else: # pragma: no cover
            return {"status": "error", "message": f"Failed to deny suggestion '{suggestion_id}'. It might have been already actioned or an issue occurred."}

    # This line should ideally not be reached if logic is correct
    return {"status": "error", "message": "Unhandled action path."} # pragma: no cover

# MANAGE_SUGGESTION_STATUS_SCHEMA = {
#    "name": "manage_suggestion_status",
#    "description": "Approves or denies a specific suggestion by its ID. Used to action suggestions based on user or system decisions.",
#    "parameters": [
#        {"name": "suggestion_id", "type": "str", "description": "The unique ID of the suggestion."},
#        {"name": "action", "type": "str", "description": "Action to perform: 'approve' or 'deny'."},
#        {"name": "reason", "type": "str", "description": "Optional reason for the action."}
#    ],
#    "returns": {"type": "dict", "description": "A dictionary with 'status' and 'message'."}
# }

if __name__ == '__main__': # pragma: no cover
    from unittest.mock import patch
    import uuid
    # Import NotificationManager for __main__ testing purposes
    from ai_assistant.core.notification_manager import NotificationManager


    print("--- Testing suggestion_management_tools.py ---")

    # Mock data for find_suggestion
    mock_suggestions_db = {
        "sugg_valid_pending": {"suggestion_id": "sugg_valid_pending", "status": "pending", "description": "A pending suggestion."},
        "sugg_valid_approved": {"suggestion_id": "sugg_valid_approved", "status": "approved", "description": "An already approved suggestion."},
    }

    def mock_find_suggestion_impl(s_id):
        return mock_suggestions_db.get(s_id)

    def mock_approve_suggestion_impl(s_id, reason=None, notification_manager=None): # Add notification_manager to mock signature
        if s_id in mock_suggestions_db:
            if mock_suggestions_db[s_id]["status"] == "pending":
                mock_suggestions_db[s_id]["status"] = "approved"
                mock_suggestions_db[s_id"]["reason_for_status"] = reason
                # Simulate notification manager being called by the actual approve_suggestion
                if notification_manager:
                    print(f"Mock approve_suggestion: Notif manager would be used for {s_id}")
                return True
        return False

    def mock_deny_suggestion_impl(s_id, reason=None, notification_manager=None): # Add notification_manager to mock signature
        if s_id in mock_suggestions_db:
            if mock_suggestions_db[s_id]["status"] == "pending":
                mock_suggestions_db[s_id]["status"] = "denied"
                mock_suggestions_db[s_id]["reason_for_status"] = reason
                if notification_manager:
                    print(f"Mock deny_suggestion: Notif manager would be used for {s_id}")
                return True
        return False

    # Dummy NotificationManager for testing tool calls, or None
    test_nm_instance = NotificationManager() # This will create a test_notifications.json if not mocked further
    # test_nm_instance = None # Alternative for tests not focusing on NM pathway

    with patch('ai_assistant.custom_tools.suggestion_management_tools.find_suggestion', side_effect=mock_find_suggestion_impl) as mocked_find, \
         patch('ai_assistant.custom_tools.suggestion_management_tools.approve_suggestion', side_effect=mock_approve_suggestion_impl) as mocked_approve, \
         patch('ai_assistant.custom_tools.suggestion_management_tools.deny_suggestion', side_effect=mock_deny_suggestion_impl) as mocked_deny:

        print("\n--- Test Case: Approve a valid pending suggestion ---")
        result_approve = manage_suggestion_status("sugg_valid_pending", "approve", "User liked it.", notification_manager=test_nm_instance)
        print(f"Result: {result_approve}")
        assert result_approve["status"] == "success"
        assert "approved" in result_approve["message"]
        assert mock_suggestions_db["sugg_valid_pending"]["status"] == "approved"

        print("\n--- Test Case: Deny a valid pending suggestion (resetting its status first) ---")
        mock_suggestions_db["sugg_valid_pending"]["status"] = "pending" # Reset for deny test
        result_deny = manage_suggestion_status("sugg_valid_pending", "deny", "Not feasible.")
        print(f"Result: {result_deny}")
        assert result_deny["status"] == "success"
        assert "denied" in result_deny["message"]
        assert mock_suggestions_db["sugg_valid_pending"]["status"] == "denied"

        print("\n--- Test Case: Attempt to approve an already approved suggestion ---")
        # Note: approve_suggestion mock currently only allows approving 'pending' ones.
        # So, this will effectively test the mock's behavior rather than a double-approval block in the tool itself.
        # The tool relies on suggestion_manager to handle actual status transition logic.
        mock_suggestions_db["sugg_already_approved"] = {"suggestion_id": "sugg_already_approved", "status": "approved"}
        result_approve_approved = manage_suggestion_status("sugg_already_approved", "approve")
        print(f"Result: {result_approve_approved}")
        assert result_approve_approved["status"] == "error" # Based on mock_approve_suggestion_impl
        assert "Failed to approve" in result_approve_approved["message"]


        print("\n--- Test Case: Suggestion ID not found ---")
        result_not_found = manage_suggestion_status("sugg_invalid", "approve")
        print(f"Result: {result_not_found}")
        assert result_not_found["status"] == "error"
        assert "not found" in result_not_found["message"]

        print("\n--- Test Case: Invalid action ---")
        result_invalid_action = manage_suggestion_status("sugg_valid_pending", "delete")
        print(f"Result: {result_invalid_action}")
        assert result_invalid_action["status"] == "error"
        assert "Invalid action" in result_invalid_action["message"]

        print("\n--- Test Case: Missing suggestion_id ---")
        result_missing_id = manage_suggestion_status("", "approve")
        print(f"Result: {result_missing_id}")
        assert result_missing_id["status"] == "error"
        assert "suggestion_id and action are required" in result_missing_id["message"]

    print("\n--- suggestion_management_tools.py tests finished ---")
