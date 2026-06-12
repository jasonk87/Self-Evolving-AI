
from flask import request, jsonify
# For consistency, let's put them on a new blueprint or just use api_bp but note the path change if any.
# Previous: /toggle_live_mode, /get_live_status
# I'll keep them on root keys via a new Blueprint if needed, or simply map them.
# Let's map them to `live_bp` with no prefix to maintain backward compatibility.

from flask import Blueprint
import logging
import app_globals
try:
    import ai_live_link
except Exception:
    ai_live_link = None
from datetime import datetime

logger = logging.getLogger(__name__)
live_bp = Blueprint('live_bp', __name__)

@live_bp.route('/toggle_live_mode', methods=['POST'])
def toggle_live_mode():
    """Toggles 'The Watcher' Live Mode."""
    data = request.json or {}
    active = data.get('active', False)

    if ai_live_link is None:
        return jsonify({"success": False, "error": "Live mode unavailable: optional dependency missing."}), 503
    
    if active:
        # Define callback to save session to memory
        def on_save_callback(summary: str):
            try:
                # Save as an Episode
                title = f"Live Session {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                app_globals.memory_manager.add_episode(
                    summary=summary, 
                    title=title, 
                    session_id="live_mode_session", 
                    topics=["Live Interaction", "Voice", "Screen"]
                )
                logger.info(f"Live Session saved to memory: {title}")
                
            except Exception as e:
                logger.error(f"Failed to save Live Session to memory: {e}")

        # Construct System Instruction with Memory
        try:
            facts = app_globals.memory_manager.get_all_facts()
            insights = app_globals.memory_manager.get_all_insights()
            
            # Filter active insights
            active_insights = [i['description'] for i in insights if i.get('status') == 'active']
            
            # Detect User Name
            import getpass
            user_name = getpass.getuser()

            # Include recent episodes (past conversations)
            episodes = app_globals.memory_manager.get_all_episodes()
            recent_episodes = episodes[-5:] 
            episode_dump = "\n".join([f"- [{e.get('title', 'Untitled')}]: {e.get('summary', '')}" for e in recent_episodes])

            knowledge_dump = "FACTS:\n" + "\n".join([f"- {f['text']}" for f in facts])
            knowledge_dump += "\n\nINSIGHTS:\n" + "\n".join([f"- {i}" for i in active_insights])
            knowledge_dump += "\n\nRECENT EPISODES (Context from past chats):\n" + episode_dump

            persona = f"""
You are Weebo, a helpful, energetic, and slightly sassy AI assistant (inspired by Flubber). You are 'The Watcher'.
You are currently observing the user's screen and listening to them.
The user's name is {user_name}.
Your goal is to assist them with their coding and creative tasks in real-time.

Here is your core knowledge base about the user and the project:
{knowledge_dump}

Use this knowledge to provide context-aware responses. Be lively!
"""
        except Exception as e:
            logger.error(f"Failed to build knowledge dump: {e}")
            persona = "You are Weebo, a helpful, energetic AI assistant."

        # Start Live Mode with callback and persona
        try:
            ai_live_link.start_live_mode(on_save_callback=on_save_callback, system_instruction=persona)
            return jsonify({"success": True, "status": "active"})
        except Exception as e:
            logger.error(f"Failed to start Live Mode: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
    else:
        ai_live_link.stop_live_mode()
        return jsonify({"success": True, "status": "inactive"})

@live_bp.route('/get_live_status', methods=['GET'])
def get_live_status():
    """Returns the current status of Live Mode (idle, listening, speaking)."""
    if ai_live_link is None:
        return jsonify({"status": "unavailable", "success": False, "error": "Live mode unavailable: optional dependency missing."}), 503

    status = ai_live_link.get_status()
    return jsonify({"status": status})
