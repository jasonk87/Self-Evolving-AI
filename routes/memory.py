
from flask import request, jsonify
from . import api_bp
import logging
import app_globals

logger = logging.getLogger(__name__)

@api_bp.route('/memory/facts', methods=['GET'])
def get_facts():
    """Returns a list of all learned facts."""
    try:
        facts = app_globals.memory_manager.get_all_facts()
        return jsonify({"facts": facts, "success": True})
    except Exception as e:
        logger.error(f"Error fetching facts: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/memory/facts', methods=['POST'])
def add_fact():
    """Adds a new fact."""
    data = request.json
    text = data.get('text')

    if not text:
        return jsonify({"error": "Fact text is required", "success": False}), 400

    try:
        new_fact = app_globals.memory_manager.add_fact(text)
        return jsonify({"fact": new_fact, "success": True})
    except Exception as e:
        logger.error(f"Error adding fact: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/memory/facts/<fact_id>', methods=['PUT'])
def update_fact(fact_id):
    """Updates an existing fact."""
    data = request.json
    text = data.get('text')

    if not text:
        return jsonify({"error": "Fact text is required", "success": False}), 400

    try:
        updated_fact = app_globals.memory_manager.update_fact(fact_id, text)
        if updated_fact:
            return jsonify({"fact": updated_fact, "success": True})
        else:
            return jsonify({"error": "Fact not found", "success": False}), 404
    except Exception as e:
        logger.error(f"Error updating fact {fact_id}: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/memory/facts/<fact_id>', methods=['DELETE'])
def delete_fact(fact_id):
    """Deletes a fact."""
    try:
        success = app_globals.memory_manager.delete_fact(fact_id)
        if success:
            return jsonify({"success": True})
        else:
            return jsonify({"error": "Fact not found", "success": False}), 404
    except Exception as e:
        logger.error(f"Error deleting fact {fact_id}: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/memory/all', methods=['GET'])
def get_all_memories():
    """Returns all memories (facts and insights) for visualization."""
    try:
        data = app_globals.memory_manager.get_all_memories_structured()
        return jsonify({"data": data, "success": True})
    except Exception as e:
        logger.error(f"Error fetching all memories: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/memory/cortex-activity', methods=['GET'])
def get_cortex_activity():
    """Returns a summary of recent memory-cortex activity."""
    try:
        lookback_hours = request.args.get('hours', default=24, type=int)
        lookback_hours = max(1, min(lookback_hours, 24 * 30))

        kinds_raw = request.args.get('kinds', default='facts,insights,episodes', type=str)
        requested_kinds = [k.strip().lower() for k in kinds_raw.split(',') if k.strip()]
        allowed_kinds = {'facts', 'insights', 'episodes'}
        selected_kinds = [k for k in requested_kinds if k in allowed_kinds] or ['facts', 'insights', 'episodes']

        source_filter = request.args.get('source', default=None, type=str)
        permanence_filter = request.args.get('permanence', default=None, type=str)

        activity = app_globals.memory_manager.get_cortex_activity_summary(
            lookback_hours=lookback_hours,
            kinds=selected_kinds,
            source=source_filter,
            permanence=permanence_filter,
        )
        return jsonify({"activity": activity, "success": True, "schema_version": 2})
    except Exception as e:
        logger.error(f"Error fetching cortex activity summary: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/memory/insights', methods=['GET'])
def get_insights():
    """Returns a list of all actionable insights."""
    try:
        insights = app_globals.memory_manager.get_all_insights()
        return jsonify({"insights": insights, "success": True})
    except Exception as e:
        logger.error(f"Error fetching insights: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/memory/insights/<insight_id>', methods=['PUT'])
def update_insight_status(insight_id):
    """Updates the status of an insight."""
    data = request.json
    status = data.get('status')

    if not status:
        return jsonify({"error": "Status is required", "success": False}), 400

    try:
        updated_insight = app_globals.memory_manager.update_insight_status(insight_id, status)
        if updated_insight:
            return jsonify({"insight": updated_insight, "success": True})
        else:
            return jsonify({"error": "Insight not found", "success": False}), 404
    except Exception as e:
        logger.error(f"Error updating insight {insight_id}: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/memory/episodes', methods=['GET'])
def get_episodes():
    """Returns a list of all episodic memories."""
    try:
        episodes = app_globals.memory_manager.get_all_episodes()
        return jsonify({"episodes": episodes, "success": True})
    except Exception as e:
        logger.error(f"Error fetching episodes: {e}")
        return jsonify({"error": str(e), "success": False}), 500
        
@api_bp.route('/memory/insights/<insight_id>', methods=['DELETE'])
def delete_insight(insight_id):
    """Deletes an insight."""
    try:
        success = app_globals.memory_manager.delete_insight(insight_id)
        if success:
            return jsonify({"success": True})
        else:
            return jsonify({"error": "Insight not found", "success": False}), 404
    except Exception as e:
        logger.error(f"Error deleting insight {insight_id}: {e}")
        return jsonify({"error": str(e), "success": False}), 500
