from types import SimpleNamespace

from flask import Flask

import app_globals
from routes import api_bp


def _build_app():
    app = Flask(__name__)
    app.register_blueprint(api_bp)
    return app


def test_config_schema_endpoint(monkeypatch):
    app = _build_app()

    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_settings_schema=lambda: {
            "AUTO_WEB_PIP": {"type": "boolean", "description": "x"},
            "DEFAULT_MODEL": {"type": "string", "description": "y"},
        }
    ))

    with app.test_client() as client:
        response = client.get('/api/config/schema')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["schema_version"] == 1
    assert "AUTO_WEB_PIP" in payload["settings"]
