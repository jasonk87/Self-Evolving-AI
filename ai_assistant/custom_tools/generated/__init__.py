"""Generated tools package.

This package may contain tools with optional third-party dependencies.
To avoid import-time crashes for unrelated tools, module imports are loaded
lazily and only exported when their dependencies are available.
"""

from importlib import import_module
from typing import Dict, Iterable, Tuple


_IMPORT_ERRORS: Dict[str, str] = {}


def _optional_exports(module_name: str, exported_names: Iterable[Tuple[str, str]]) -> None:
    """Attempt to import a generated module and expose selected symbols.

    Args:
        module_name: Module path relative to this package.
        exported_names: Iterable of (attribute_name_in_module, alias_to_export).
    """
    try:
        module = import_module(f"{__name__}.{module_name}")
    except Exception as exc:
        _IMPORT_ERRORS[module_name] = f"{type(exc).__name__}: {exc}"
        return

    for attr_name, alias in exported_names:
        attr = getattr(module, attr_name, None)
        if attr is not None:
            globals()[alias] = attr


def get_generated_tool_import_errors() -> Dict[str, str]:
    """Return modules that failed optional import and the captured reason."""
    return dict(_IMPORT_ERRORS)


_OPTIONAL_EXPORTS = [
    ("scheduler_tool", [("set_reminder", "set_scheduled_reminder")]),
    ("set_bedtime_reminder", [("set_bedtime_reminder", "set_bedtime_reminder")]),
    ("reminder_tool", [("set_reminder", "set_os_reminder")]),
    ("set_reminder", [("set_reminder", "set_reminder")]),
    ("reminder_scheduler", [("schedule_reminder", "schedule_reminder")]),
    ("weather_tool", [("get_weather", "get_weather")]),
    ("remind_at_3_40", [("remind_at_3_40", "remind_at_3_40")]),
    ("twilio_text_tool", [("send_text_message", "send_text_message")]),
    ("ai_response_generator", [("generate_two_responses", "generate_two_responses")]),
    ("location_utils", [("get_location_by_ip", "get_location_by_ip")]),
    ("generate_safe_html", [("generate_safe_html", "generate_safe_html")]),
    ("chat_html_tool", [("chat_dynamic_html", "chat_dynamic_html")]),
    ("div_table_generator", [("generate_div_table_html", "generate_div_table_html")]),
    ("notification_scheduler", [("schedule_notification", "schedule_notification")]),
    ("suggestion_tool", [("list_suggestions", "list_suggestions")]),
]

for module_name, exports in _OPTIONAL_EXPORTS:
    _optional_exports(module_name, exports)
