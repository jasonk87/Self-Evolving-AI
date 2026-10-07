"""Integrations: abilities Codex doesn't have on its own.

Weather, approximate location, web/news/image search, deep research, Google Calendar, SMS and chat widgets.
They use the user's own keys from ``.env`` and are exposed to the Codex brain through one dynamic tool
(``use_integration``). Outward-facing ones (calendar writes, texts) ask the user first.
"""

from __future__ import annotations

import inspect
import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .. import log
from . import gcalendar, search, sms, weather, widgets
from .base import IntegrationError, Outcome

if TYPE_CHECKING:
    from ..app import WeeboApp

logger = log.get("integrations")

_GOOGLE = ("GOOGLE_API_KEY", "GOOGLE_CSE_ID")


@dataclass(frozen=True)
class Integration:
    name: str
    about: str
    fn: Callable[..., Awaitable[Outcome]]
    risk: str = "read"  # read | confirm (asks the user first)
    env: tuple[str, ...] = ()
    calendar: bool = False

    def params(self) -> set[str]:
        return {p for p in inspect.signature(self.fn).parameters if p != "app"}

    def required(self) -> set[str]:
        return {n for n, p in inspect.signature(self.fn).parameters.items()
                if n != "app" and p.default is inspect.Parameter.empty}


CATALOG: dict[str, Integration] = {i.name: i for i in (
    Integration("get_weather", "Current weather. Args: location (e.g. 'Austin, TX').", weather.get_weather,
                env=("OPENWEATHER_API_KEY",)),
    Integration("get_user_location", "The user's approximate location from this machine's IP. No args.",
                weather.get_user_location),
    Integration("google_search", "Web search: Google Custom Search with the user's keys, DuckDuckGo otherwise. "
                "Args: query, num_results.", search.google_search),
    Integration("news_search", "Fresh, dated news headlines. Args: query, num_results.", search.news_search),
    Integration("execute_deep_research", "Multi-source research: searches, reads the top pages and writes a sourced "
                "answer. Args: query.", search.execute_deep_research),
    Integration("web_search_images", "Finds images and shows them in the chat. Args: query, num_images.",
                search.web_search_images, env=_GOOGLE),
    Integration("check_calendar", "Google Calendar agenda for a day. Args: date_str ('today', 'tomorrow' or "
                "YYYY-MM-DD).", gcalendar.check_calendar, calendar=True),
    Integration("list_upcoming_events", "Next Google Calendar events. Args: max_results.",
                gcalendar.list_upcoming_events, calendar=True),
    Integration("schedule_event", "Create a Google Calendar event. Args: summary, datetime_str (ISO), duration_mins, "
                "description.", gcalendar.schedule_event, risk="confirm", calendar=True),
    Integration("send_text_message", "Send an SMS through the user's Twilio account. Args: recipient, message.",
                sms.send_text_message, risk="confirm",
                env=("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER")),
    Integration("generate_bar_chart_html", "Render a bar chart widget. Args: labels[], values[], title, orientation, "
                "bar_colors[].", widgets.generate_bar_chart_html),
    Integration("generate_div_table_html", "Render a table widget. Args: data (list of rows; first row is the "
                "header), header.", widgets.generate_div_table_html),
    Integration("chat_dynamic_html", "Generate a small interactive HTML widget from an idea. Args: idea.",
                widgets.chat_dynamic_html),
)}


def missing_requirements(name: str) -> list[str]:
    """What an integration still needs on this machine (empty = ready)."""
    item = CATALOG[name]
    missing = [var for var in item.env if not os.environ.get(var)]
    if item.calendar:
        missing += gcalendar.libraries_missing()
        if gcalendar.credentials_path() is None:
            missing.append("Google Calendar credentials.json")
    return missing


class Integrations:
    def __init__(self, app: "WeeboApp"):
        self.app = app

    @staticmethod
    def describe_catalog() -> str:
        lines = []
        for name, item in CATALOG.items():
            missing = missing_requirements(name)
            note = f" UNAVAILABLE (needs {', '.join(missing)}); use another way." if missing else ""
            lines.append(f"- {name}: {item.about}{' Asks the user first.' if item.risk == 'confirm' else ''}{note}")
        return "\n".join(lines)

    def snapshot(self) -> dict[str, Any]:
        return {"tools": [{"name": n, "risk": i.risk, "about": i.about, "missing": missing_requirements(n)}
                          for n, i in CATALOG.items()]}

    def ready_count(self) -> int:
        return sum(1 for name in CATALOG if not missing_requirements(name))

    async def run(self, name: str, arguments: dict[str, Any], conversation_id: str | None = None,
                  task_id: str | None = None) -> tuple[str, bool, dict[str, Any]]:
        """Returns (text for the model, success, extras with html/images for the chat UI)."""
        item = CATALOG.get(name)
        if item is None:
            return f"'{name}' is not an integration. Available: {', '.join(CATALOG)}", False, {}
        arguments = dict(arguments or {})
        unknown = sorted(set(arguments) - item.params())
        if unknown:
            return f"{name} doesn't take: {', '.join(unknown)}. It takes: {', '.join(sorted(item.params())) or 'nothing'}.", False, {}
        absent = sorted(item.required() - set(arguments))
        if absent:
            return f"{name} needs: {', '.join(absent)}.", False, {}
        missing = missing_requirements(name)
        if missing:
            return (f"{name} isn't set up on this machine (needs {', '.join(missing)}). Use another approach, such as "
                    "your own web search, and tell the user how to enable it."), False, {}
        if item.risk == "confirm":
            pretty = json.dumps(arguments, ensure_ascii=False, indent=1)[:1500]
            approved = await self.app.interactions.confirm(f"Use integration: {name}", f"{name}({pretty})",
                                                           conversation_id=conversation_id, task_id=task_id)
            if not approved:
                return "The user declined this action.", False, {}
        try:
            outcome = await item.fn(self.app, **arguments)
        except IntegrationError as exc:
            return f"{name}: {exc}", False, {}
        except Exception as exc:
            logger.warning("Integration %s failed: %r", name, exc)
            self.app.diagnostics.record("integration_failed", f"Integration {name} failed: {exc!r}", {"tool": name})
            return f"{name} failed: {exc}", False, {}
        return outcome.text[:20000], True, outcome.extras
