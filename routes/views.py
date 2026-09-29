
from flask import render_template, current_app, jsonify
from . import views_bp
import app_globals
import time

@views_bp.route('/')
def index():
    return render_template('index.html')

@views_bp.route('/health')
def health():
    """Small unauthenticated liveness/readiness probe with no secret data."""
    checks = {
        "controller": app_globals.controller is not None,
        "chat_manager": app_globals.chat_manager is not None,
        "memory_manager": app_globals.memory_manager is not None,
        "ai_loop": app_globals.ai_loop is not None and not bool(getattr(app_globals.ai_loop, "is_closed", lambda: False)()),
    }
    healthy = all(checks.values())
    return jsonify({
        "ok": healthy,
        "status": "ready" if healthy else "degraded",
        "checks": checks,
        "timestamp": time.time(),
    }), (200 if healthy else 503)

@views_bp.route('/favicon.ico')
def favicon():
    return current_app.send_static_file('favicon.ico')

@views_bp.route('/service-worker.js')
def service_worker():
    response = current_app.send_static_file('service-worker.js')
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache'
    return response
