import ai_assistant.custom_tools.generated as generated_pkg
from ai_assistant.tools.tool_system import get_tool


def test_generated_tools_import_error_registry_shape():
    errors = generated_pkg.get_generated_tool_import_errors()
    assert isinstance(errors, dict)
    for module_name, reason in errors.items():
        assert isinstance(module_name, str)
        assert isinstance(reason, str)
        assert reason


def test_generated_tools_known_exports_remain_accessible():
    # These should remain import-safe regardless of optional dependency failures.
    assert hasattr(generated_pkg, "generate_safe_html")
    assert hasattr(generated_pkg, "generate_div_table_html")


def test_generated_html_and_weather_tools_are_registered():
    assert get_tool("chat_dynamic_html")["module_path"].endswith(".chat_html_tool")
    assert get_tool("generate_safe_html")["module_path"].endswith(".generate_safe_html")
    assert get_tool("get_weather")["module_path"].endswith(".weather_tool")
