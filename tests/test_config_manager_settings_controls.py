import ai_assistant.config as config_module
from ai_assistant.core.config_manager import ConfigManager


def test_settings_schema_includes_runtime_controls():
    manager = ConfigManager()
    schema = manager.get_settings_schema()

    assert "AUTO_WEB_PIP" in schema
    assert schema["AUTO_WEB_PIP"]["type"] == "boolean"
    assert "REMINDER_CHECK_INTERVAL_SECONDS" in schema
    assert schema["REMINDER_CHECK_INTERVAL_SECONDS"]["type"] == "integer"
    assert "DREAM_INTERVAL_SECONDS" in schema
    assert "ENABLE_DREAM_MODE" in schema


def test_coerce_setting_value_validates_enum():
    manager = ConfigManager()

    value = manager.coerce_setting_value("DEFAULT_EXECUTION_MODE", "THINKING_PRO")
    assert value == "THINKING_PRO"

    try:
        manager.coerce_setting_value("DEFAULT_EXECUTION_MODE", "INVALID")
        assert False, "Expected ValueError for invalid enum"
    except ValueError:
        assert True


def test_update_setting_rejects_unmanaged_keys():
    manager = ConfigManager()
    current = getattr(config_module, "ENABLE_THINKING")

    assert manager.update_setting("NOT_A_REAL_SETTING", 123) is False
    assert getattr(config_module, "ENABLE_THINKING") == current
