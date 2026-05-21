
from typing import Dict, Any, Optional
from flask import request, jsonify
from . import api_bp
import logging
from ai_assistant.custom_tools.file_system_tools import (
    list_project_files,
    get_project_file_content,
    save_project_file_content
)

logger = logging.getLogger(__name__)

@api_bp.route('/files/list', methods=['GET'])
def list_files() -> Any:
    """Lists files for a given project and subdirectory."""
    project_name: Optional[str] = request.args.get('project_name')
    path: str = request.args.get('path', '')

    if not project_name:
        return jsonify({"error": "Project name is required", "success": False}), 400

    try:
        result: Dict[str, Any] = list_project_files(project_name, path)
        if result['status'] == 'error':
            return jsonify({"error": result['message'], "success": False}), 400
        return jsonify({"files": result['files'], "directories": result['directories'], "path": result['path_listed'], "success": True})
    except Exception as e:
        logger.error(f"Error listing files for project {project_name}: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/files/read', methods=['GET'])
def read_file() -> Any:
    """Reads the content of a file."""
    project_name: Optional[str] = request.args.get('project_name')
    path: Optional[str] = request.args.get('path')

    if not project_name or not path:
        return jsonify({"error": "Project name and path are required", "success": False}), 400

    try:
        result: Dict[str, Any] = get_project_file_content(project_name, path)
        if result['status'] == 'error':
            return jsonify({"error": result['message'], "success": False}), 400
        return jsonify({"content": result['content'], "file_path": result['file_path'], "success": True})
    except Exception as e:
        logger.error(f"Error reading file {path} for project {project_name}: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/files/save', methods=['POST'])
def save_file() -> Any:
    """Saves content to a file."""
    data: Dict[str, Any] = request.json or {}
    project_name: Optional[str] = data.get('project_name')
    path: Optional[str] = data.get('path')
    content: Optional[str] = data.get('content')

    if not project_name or not path or content is None:
        return jsonify({"error": "Project name, path, and content are required", "success": False}), 400

    try:
        result: Dict[str, Any] = save_project_file_content(project_name, path, content)
        if result['status'] == 'error':
            return jsonify({"error": result['message'], "success": False}), 400
        return jsonify({"file_path": result['file_path'], "success": True})
    except Exception as e:
        logger.error(f"Error saving file {path} for project {project_name}: {e}")
        return jsonify({"error": str(e), "success": False}), 500
