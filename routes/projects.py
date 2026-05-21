
from flask import jsonify
from . import api_bp
import logging
from ai_assistant.core.project_manager import list_projects

logger = logging.getLogger(__name__)

@api_bp.route('/projects', methods=['GET'])
def get_projects():
    """Returns a list of all projects."""
    try:
        projects = list_projects()
        return jsonify({"projects": projects, "success": True})
    except Exception as e:
        logger.error(f"Error listing projects: {e}")
        return jsonify({"error": str(e), "success": False}), 500
