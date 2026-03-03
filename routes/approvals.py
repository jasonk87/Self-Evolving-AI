
from flask import request, jsonify
from . import api_bp
import logging
import app_globals
from ai_assistant.core.approval_manager import approval_manager
from ai_assistant.learning.learning import ActionableInsight, InsightType
from ai_assistant.core.task_manager import ActiveTaskStatus
from dataclasses import asdict

logger = logging.getLogger(__name__)

def serialize_approval_data(data):
    if isinstance(data, ActionableInsight):
        d = asdict(data)
        d['type'] = data.type.name # Enum to string
        return d
    return data # Fallback

# --- Mission Control Endpoints ---

@api_bp.route('/tasks', methods=['GET'])
def list_active_tasks():
    """Returns a list of all active tasks."""
    if not app_globals.orchestrator:
         return jsonify({"error": "System starting up...", "success": False}), 503

    try:
        tasks = app_globals.orchestrator.task_manager.list_active_tasks()
        tasks_data = [t.to_dict() for t in tasks]
        return jsonify({"tasks": tasks_data, "success": True})
    except Exception as e:
        logger.error(f"Error listing active tasks: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/tasks/<task_id>/stop', methods=['POST'])
def stop_task(task_id):
    """Cancels a specific task."""
    try:
        reason = request.json.get('reason', 'User cancelled via Mission Control') if request.json else 'User cancelled'
        task = app_globals.orchestrator.task_manager.get_task(task_id)
        if not task:
            return jsonify({"error": "Task not found", "success": False}), 404
            
        app_globals.orchestrator.task_manager.update_task_status(
            task_id, 
            ActiveTaskStatus.USER_CANCELLED, 
            reason=reason
        )
        return jsonify({"success": True, "message": f"Task {task_id} cancelled."})
    except Exception as e:
        logger.error(f"Error stopping task {task_id}: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/tasks/<task_id>/message', methods=['POST'])
def message_task(task_id):
    """Injects a user message into the task's context."""
    try:
        message = request.json.get('message')
        if not message:
            return jsonify({"error": "No message provided", "success": False}), 400
            
        task = app_globals.orchestrator.task_manager.get_task(task_id)
        if not task:
            return jsonify({"error": "Task not found", "success": False}), 404
        
        # We store the feedback in details. Check if 'user_feedback' list exists
        if 'user_feedback' not in task.details or not isinstance(task.details['user_feedback'], list):
            task.details['user_feedback'] = []
            
        task.details['user_feedback'].append({
            "timestamp": app_globals.config_manager.get_time(), # or just datetime.now().isoformat()
            "message": message
        })
        
        # Trigger an update so UI sees it (optional, but good for confirmation)
        app_globals.orchestrator.task_manager._save_active_tasks()
        # Also notify via socket potentially? Or let polling handle it.
        
        return jsonify({"success": True, "message": "Feedback injected."})
    except Exception as e:
        logger.error(f"Error messaging task {task_id}: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/approvals', methods=['GET'])
def get_approvals():
    """Lists all pending approval requests, including persistent Actionable Insights."""
    try:
        # 1. Get transient requests from ApprovalManager
        requests = approval_manager.get_pending_requests()
        serialized_requests = []
        for req in requests:
            req_copy = req.copy()
            if 'execute_func' in req_copy:
                del req_copy['execute_func']
            req_copy['data'] = serialize_approval_data(req_copy['data'])
            # Ensure it has a source tag
            req_copy['source'] = 'approval_manager'
            serialized_requests.append(req_copy)

        # 2. Get persistent 'NEW' insights from LearningAgent
        if app_globals.orchestrator and app_globals.orchestrator.learning_agent:
            pending_insights = [
                i for i in app_globals.orchestrator.learning_agent.insights 
                if i.status in ["NEW", "SELF_HEALING_PROPOSED"] 
                and i.type in [
                    InsightType.TOOL_BUG_SUSPECTED, 
                    InsightType.TOOL_ENHANCEMENT_SUGGESTED
                ]
            ]
            
            for insight in pending_insights:
                # Map Insight to Approval Request Format temporarily for UI
                insight_req = {
                    "id": insight.insight_id, # Standardize on 'id' for frontend
                    "type": insight.type.name.lower(), # Use the actual type name (e.g., 'tool_bug_suspected')
                    "description": insight.description,
                    "created_at": insight.creation_timestamp,
                    "data": serialize_approval_data(insight),
                    "source": "learning_agent"
                }
                serialized_requests.append(insight_req)

        return jsonify({"approvals": serialized_requests, "success": True})
    except Exception as e:
        logger.error(f"Error listing approvals: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/approvals/<req_id>/approve', methods=['POST'])
async def approve_request(req_id):
    """Approves a request (either transient or persistent insight)."""
    try:
        feedback = request.json.get('feedback') if request.json else None

        # 1. Try ApprovalManager first
        if approval_manager.get_request(req_id):
            if feedback:
                logger.info(f"User approved request {req_id} with feedback: {feedback}")
            success = await approval_manager.approve_request(req_id)
            if success: return jsonify({"success": True})

        # 2. Try LearningAgent Insights
        if app_globals.orchestrator and app_globals.orchestrator.learning_agent:
            insight = next((i for i in app_globals.orchestrator.learning_agent.insights if i.insight_id == req_id), None)
            if insight:
                if feedback:
                    if not insight.metadata: insight.metadata = {}
                    insight.metadata['user_feedback_on_approval'] = feedback
                    app_globals.memory_manager.add_fact(f"User Approved Insight {req_id} with feedback: {feedback}")

                if insight.type in [InsightType.TOOL_BUG_SUSPECTED, InsightType.TOOL_ENHANCEMENT_SUGGESTED]:
                     success = await app_globals.orchestrator.learning_agent.execute_self_healing_for_insight(insight, apply_immediately=True)
                else:
                    app_globals.orchestrator.learning_agent._save_insights()
                    success = True # Just mark as saved/approved for now
                
                if success:
                     return jsonify({"success": True, "message": "Insight execution triggered."})
                else:
                     return jsonify({"success": False, "error": "Insight execution failed. The insight might be missing required information."}), 400

        return jsonify({"error": "Request not found", "success": False}), 404
    except Exception as e:
        logger.error(f"Error approving request {req_id}: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/approvals/<req_id>/deny', methods=['POST'])
def deny_request(req_id):
    """Denies a request."""
    try:
        feedback = request.json.get('feedback') if request.json else None

        # 1. Try ApprovalManager
        if approval_manager.get_request(req_id):
            if feedback:
                logger.info(f"User denied request {req_id} with feedback: {feedback}")
            success = approval_manager.deny_request(req_id)
            if success: return jsonify({"success": True})

        # 2. Try LearningAgent Insights
        if app_globals.orchestrator and app_globals.orchestrator.learning_agent:
            insight = next((i for i in app_globals.orchestrator.learning_agent.insights if i.insight_id == req_id), None)
            if insight:
                insight.status = "REJECTED_BY_USER"
                if feedback:
                    if not insight.metadata: insight.metadata = {}
                    insight.metadata['user_rejection_reason'] = feedback
                    app_globals.memory_manager.add_fact(f"User rejected insight '{insight.description}' with reason: {feedback}")
                
                app_globals.orchestrator.learning_agent._save_insights()
                return jsonify({"success": True})

        return jsonify({"error": "Request not found", "success": False}), 404
    except Exception as e:
        logger.error(f"Error denying request {req_id}: {e}")
        return jsonify({"error": str(e), "success": False}), 500
