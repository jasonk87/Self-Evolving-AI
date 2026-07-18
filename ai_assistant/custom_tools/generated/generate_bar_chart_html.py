import html
import math
import re
from typing import List, Optional


_DEFAULT_COLORS = (
    "#4CAF50",
    "#2196F3",
    "#FF9800",
    "#9C27B0",
    "#F44336",
    "#00BCD4",
    "#FFEB3B",
    "#E91E63",
)


def is_safe_color(color: str) -> bool:
    """Allow plain CSS color names, hex colors, and bounded rgb/rgba colors."""
    if not isinstance(color, str):
        return False

    value = color.strip()
    if re.fullmatch(r"#[0-9a-fA-F]{3,8}", value):
        return True
    if re.fullmatch(r"[a-zA-Z]{1,32}", value):
        return True

    match = re.fullmatch(r"rgba?\(([^()]*)\)", value, flags=re.IGNORECASE)
    if not match:
        return False

    parts = [part.strip() for part in match.group(1).split(",")]
    expected_parts = 4 if value.casefold().startswith("rgba") else 3
    if len(parts) != expected_parts:
        return False

    try:
        channels = [int(part) for part in parts[:3]]
        if any(channel < 0 or channel > 255 for channel in channels):
            return False
        if expected_parts == 4:
            alpha = float(parts[3])
            if not math.isfinite(alpha) or alpha < 0 or alpha > 1:
                return False
    except (TypeError, ValueError):
        return False

    return True


def _error(message: str) -> str:
    return f'<div class="bar-chart-error">{html.escape(message)}</div>'


def _bounded_dimension(value: int, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def generate_bar_chart_html(
    labels: List[str],
    values: List[float],
    title: Optional[str] = None,
    bar_colors: Optional[List[str]] = None,
    width: int = 400,
    height: int = 200,
    orientation: str = "horizontal",
) -> str:
    """Generate a responsive, self-contained HTML bar chart for chat output."""
    if not labels or not values or len(labels) != len(values):
        return _error("Labels and values must be non-empty lists of equal length.")

    numeric_values = []
    for value in values:
        if isinstance(value, bool):
            return _error("Values must contain finite, non-negative numbers.")
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return _error("Values must contain finite, non-negative numbers.")
        if not math.isfinite(numeric_value) or numeric_value < 0:
            return _error("Values must contain finite, non-negative numbers.")
        numeric_values.append(numeric_value)

    colors = list(bar_colors) if bar_colors else list(_DEFAULT_COLORS)
    if not colors or any(not is_safe_color(color) for color in colors):
        return _error("Bar colors must be safe CSS color names, hex values, or rgb/rgba values.")
    colors = [colors[index % len(colors)].strip() for index in range(len(labels))]

    chart_width = _bounded_dimension(width, 400, 160, 1600)
    chart_height = _bounded_dimension(height, 200, 120, 1000)
    normalized_orientation = str(orientation or "horizontal").strip().casefold()
    if normalized_orientation not in {"horizontal", "vertical"}:
        return _error("Orientation must be either horizontal or vertical.")

    max_value = max(numeric_values, default=0.0)
    percentages = [0.0 if max_value <= 0 else value / max_value * 100 for value in numeric_values]
    escaped_title = html.escape(str(title)) if title else ""
    aria_title = html.escape(str(title or "Bar chart"), quote=True)

    chart_parts = [
        (
            f'<div class="bar-chart-container" role="img" aria-label="{aria_title}" '
            f'style="box-sizing:border-box;width:100%;max-width:{chart_width}px;overflow:hidden;'
            'font-family:sans-serif;border:1px solid #ccc;border-radius:8px;padding:12px;">'
        )
    ]
    if escaped_title:
        chart_parts.append(
            f'<div class="bar-chart-title" style="text-align:center;font-weight:700;margin-bottom:12px;">{escaped_title}</div>'
        )

    if normalized_orientation == "horizontal":
        chart_parts.append('<div class="bar-chart-rows" style="display:flex;flex-direction:column;gap:8px;">')
        for label, original_value, percentage, color in zip(labels, values, percentages, colors):
            escaped_label = html.escape(str(label))
            escaped_value = html.escape(str(original_value))
            chart_parts.append(
                '<div class="bar-chart-row" style="display:grid;grid-template-columns:minmax(0,80px) '
                'minmax(0,1fr) auto;align-items:center;gap:8px;min-width:0;">'
                f'<div class="bar-chart-label" title="{html.escape(str(label), quote=True)}" '
                'style="text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
                f'{escaped_label}</div>'
                '<div class="bar-chart-track" style="height:24px;min-width:0;background:#edf1f5;overflow:hidden;border-radius:4px;">'
                f'<div class="bar-chart-bar" style="height:100%;width:{percentage:.1f}%;background:{color};border-radius:4px;"></div>'
                '</div>'
                f'<div class="bar-chart-value" style="white-space:nowrap;">{escaped_value}</div>'
                '</div>'
            )
        chart_parts.append('</div>')
    else:
        chart_parts.append(
            f'<div class="bar-chart-columns" style="display:flex;align-items:stretch;gap:8px;height:{chart_height}px;min-width:0;">'
        )
        for label, original_value, percentage, color in zip(labels, values, percentages, colors):
            escaped_label = html.escape(str(label))
            escaped_value = html.escape(str(original_value))
            chart_parts.append(
                '<div class="bar-chart-column" style="display:flex;flex:1 1 0;min-width:0;flex-direction:column;align-items:center;">'
                f'<div class="bar-chart-value" style="white-space:nowrap;">{escaped_value}</div>'
                '<div class="bar-chart-track" style="display:flex;align-items:flex-end;flex:1;width:100%;min-height:0;'
                'background:#edf1f5;overflow:hidden;border-radius:4px;">'
                f'<div class="bar-chart-bar" style="width:100%;height:{percentage:.1f}%;background:{color};border-radius:4px 4px 0 0;"></div>'
                '</div>'
                f'<div class="bar-chart-label" title="{html.escape(str(label), quote=True)}" '
                'style="box-sizing:border-box;width:100%;padding-top:6px;text-align:center;white-space:nowrap;'
                f'overflow:hidden;text-overflow:ellipsis;">{escaped_label}</div>'
                '</div>'
            )
        chart_parts.append('</div>')

    chart_parts.append('</div>')
    return "\n".join(chart_parts)
