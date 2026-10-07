"""Chart, table and free-form HTML widgets rendered in a sandboxed frame under Weebo's message."""

from __future__ import annotations

import html
import math
import re
from typing import TYPE_CHECKING, Any

from .base import IntegrationError, Outcome

if TYPE_CHECKING:
    from ..app import WeeboApp

DEFAULT_COLORS = ("#4CAF50", "#2196F3", "#FF9800", "#9C27B0", "#F44336", "#00BCD4", "#FFEB3B", "#E91E63")
RENDERED = "Rendered the widget in the chat for the user."


def is_safe_color(color: Any) -> bool:
    """Plain CSS color names, hex colors, and bounded rgb()/rgba()."""
    if not isinstance(color, str):
        return False
    value = color.strip()
    if re.fullmatch(r"#[0-9a-fA-F]{3,8}|[a-zA-Z]{1,32}", value):
        return True
    match = re.fullmatch(r"rgba?\(([^()]*)\)", value, flags=re.IGNORECASE)
    if not match:
        return False
    parts = [p.strip() for p in match.group(1).split(",")]
    if len(parts) != (4 if value.lower().startswith("rgba") else 3):
        return False
    try:
        if any(not 0 <= int(p) <= 255 for p in parts[:3]):
            return False
        return len(parts) == 3 or (math.isfinite(float(parts[3])) and 0 <= float(parts[3]) <= 1)
    except ValueError:
        return False


def bar_chart_html(labels: list[Any], values: list[Any], title: str = "", colors: list[str] | None = None,
                   orientation: str = "horizontal", height: int = 220) -> str:
    if not labels or not values or len(labels) != len(values):
        raise IntegrationError("labels and values must be non-empty lists of the same length.")
    numbers = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise IntegrationError("values must be numbers.") from None
        if isinstance(value, bool) or not math.isfinite(number) or number < 0:
            raise IntegrationError("values must be finite, non-negative numbers.")
        numbers.append(number)
    palette = list(colors or DEFAULT_COLORS)
    if any(not is_safe_color(c) for c in palette):
        raise IntegrationError("colors must be CSS color names, hex values or rgb()/rgba().")
    orientation = (orientation or "horizontal").lower()
    if orientation not in ("horizontal", "vertical"):
        raise IntegrationError("orientation must be horizontal or vertical.")
    top = max(numbers) or 1.0
    height = max(120, min(1000, int(height or 220)))
    rows = []
    for i, (label, raw, number) in enumerate(zip(labels, values, numbers)):
        color, pct = palette[i % len(palette)].strip(), number / top * 100
        name, shown = html.escape(str(label)), html.escape(str(raw))
        if orientation == "horizontal":
            rows.append(f'<div class="row"><div class="label" title="{name}">{name}</div><div class="track">'
                        f'<div class="bar" style="width:{pct:.1f}%;background:{color}"></div></div>'
                        f'<div class="value">{shown}</div></div>')
        else:
            rows.append(f'<div class="col"><div class="value">{shown}</div><div class="track">'
                        f'<div class="bar" style="height:{pct:.1f}%;background:{color}"></div></div>'
                        f'<div class="label" title="{name}">{name}</div></div>')
    style = (
        "body{margin:0;font:14px system-ui,sans-serif;color:#1f2933}.chart{padding:12px}"
        "h4{margin:0 0 10px;text-align:center}.track{background:#edf1f5;border-radius:4px;overflow:hidden}"
        ".label{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.value{white-space:nowrap}"
        ".row{display:grid;grid-template-columns:minmax(0,90px) minmax(0,1fr) auto;gap:8px;align-items:center;margin:6px 0}"
        ".row .track{height:22px}.row .bar{height:100%;border-radius:4px}"
        f".cols{{display:flex;gap:8px;height:{height}px}}.col{{flex:1;min-width:0;display:flex;flex-direction:column;"
        "align-items:center}.col .track{flex:1;width:100%;display:flex;align-items:flex-end}"
        ".col .bar{width:100%;border-radius:4px 4px 0 0}.col .label{width:100%;text-align:center;padding-top:4px}"
        "@media (prefers-color-scheme:dark){body{color:#e4e7eb}.track{background:#2a3138}}"
    )
    heading = f"<h4>{html.escape(title)}</h4>" if title else ""
    body = "".join(rows)
    wrapper = f'<div class="cols">{body}</div>' if orientation == "vertical" else body
    label = html.escape(title or "Bar chart", quote=True)
    return f'<style>{style}</style><div class="chart" role="img" aria-label="{label}">{heading}{wrapper}</div>'


def table_html(rows: list[list[Any]], header: bool = True) -> str:
    if not rows or not all(isinstance(r, list) for r in rows):
        raise IntegrationError("data must be a list of rows (each row a list of cells).")
    style = ("body{margin:0;font:14px system-ui,sans-serif;color:#1f2933}table{border-collapse:collapse;width:100%}"
             "th,td{border:1px solid #d9dee3;padding:6px 8px;text-align:left}th{background:#f3f5f7}"
             "@media (prefers-color-scheme:dark){body{color:#e4e7eb}th{background:#2a3138}th,td{border-color:#3a434c}}")
    out = []
    for index, row in enumerate(rows[:500]):
        tag = "th" if header and index == 0 else "td"
        out.append("<tr>" + "".join(f"<{tag}>{html.escape(str(cell))}</{tag}>" for cell in row[:50]) + "</tr>")
    return f"<style>{style}</style><table>{''.join(out)}</table>"


async def generate_bar_chart_html(app: "WeeboApp", labels: list[Any], values: list[Any], title: str = "",
                                  orientation: str = "horizontal", bar_colors: list[str] | None = None) -> Outcome:
    return Outcome(RENDERED, {"html": bar_chart_html(labels, values, title, bar_colors, orientation)})


async def generate_div_table_html(app: "WeeboApp", data: list[list[Any]], header: bool = True) -> Outcome:
    return Outcome(RENDERED, {"html": table_html(data, header)})


WIDGET_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["html", "css", "js"],
    "properties": {"html": {"type": "string"}, "css": {"type": "string"}, "js": {"type": "string"}},
}


async def chat_dynamic_html(app: "WeeboApp", idea: str) -> Outcome:
    if not (idea or "").strip():
        raise IntegrationError("Describe the widget you want.")
    prompt = ("Build a small, self-contained interactive HTML widget for a chat message. No external URLs, fonts or "
              "network calls; it runs in a sandboxed frame. Keep it compact and readable in light and dark mode.\n\n"
              f"Idea: {idea}\n\nReturn JSON with html (body markup), css and js (either may be empty).")
    result = await app.mind.think(prompt, WIDGET_SCHEMA, label="widget", count=False)
    if not isinstance(result, dict) or not str(result.get("html") or "").strip():
        raise IntegrationError("The widget came back empty; try describing it differently.")
    css, js = str(result.get("css") or ""), str(result.get("js") or "")
    page = (f"<style>{css}</style>" if css.strip() else "") + str(result["html"]) + (f"<script>{js}</script>" if js.strip() else "")
    return Outcome(RENDERED, {"html": page})
