import ai_assistant.custom_tools.generated as generated_pkg


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
