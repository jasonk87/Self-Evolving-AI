from types import SimpleNamespace

from flask import Flask

import app_globals
from routes import api_bp


def _build_app():
    app = Flask(__name__)
    app.register_blueprint(api_bp)
    return app


def test_config_update_coerces_and_updates(monkeypatch):
    app = _build_app()

    updates = {}

    def coerce_setting_value(key, value):
        if key == "AUTO_WEB_PIP":
            return str(value).lower() == "true"
        return value

    def update_setting(key, value):
        updates[key] = value
        return True

    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        coerce_setting_value=coerce_setting_value,
        update_setting=update_setting,
    ))

    with app.test_client() as client:
        response = client.post('/api/config', json={"AUTO_WEB_PIP": "true"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert updates["AUTO_WEB_PIP"] is True


def test_config_update_returns_400_on_invalid_value(monkeypatch):
    app = _build_app()

    def coerce_setting_value(key, value):
        raise ValueError("invalid")

    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        coerce_setting_value=coerce_setting_value,
        update_setting=lambda key, value: True,
    ))

    with app.test_client() as client:
        response = client.post('/api/config', json={"AUTO_WEB_PIP": "maybe"})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert payload["errors"]
