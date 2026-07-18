import html
import re
from typing import List, Optional

def is_safe_color(color: str) -> bool:
    """
    Validates that a color string is safe for use in a style attribute.
    Only allows alphanumeric characters, spaces, '#', '(', ')', ',', '.', and '%'.
    This prevents injection of harmful CSS or expressions.
    """
    return bool(re.fullmatch(r'[a-zA-Z0-9#(),.\s%]+', color))

def generate_bar_chart_html(
    labels: List[str],
    values: List[float],
    title: Optional[str] = None,
    bar_colors: Optional[List[str]] = None,
    width: int = 400,
    height: int = 200,
    orientation: str = "horizontal"
) -> str:
    """
    Generates a safe, self‑contained HTML bar chart using only div elements and inline CSS.

    The chart is horizontal or vertical, displays the provided labels and numeric values,
    and can include a title and custom bar colors. All user‑provided text is escaped to
    prevent XSS, and bar colors are validated to ensure no harmful code is injected. The
    resulting string is suitable for direct display in chat environments and for sanitization
    by tools like DOMPurify.

    Args:
        labels (List[str]): The labels for each bar (same length as values).
        values (List[float]): The numeric value for each bar (same length as labels).
        title (Optional[str]): An optional title displayed above the chart. Defaults to None.
        bar_colors (Optional[List[str]]): Optional list of CSS color strings, one per bar.
            If not provided, a default dark gray is used. If the list is shorter than the
            number of bars, remaining bars use the default color. Colors are validated;
            any unsafe color is replaced by the default. Defaults to None.
        width (int): Total chart width in pixels. Defaults to 400.
        height (int): Total chart height in pixels. Defaults to 200.
        orientation (str): "horizontal" (bars from left to right) or "vertical" (bars from bottom to top).
            Defaults to "horizontal".

    Returns:
        str: A string containing sanitized HTML and inline CSS. No script tags or event
        handlers are present.

    Raises:
        ValueError: If the lengths of labels and values do not match, or if orientation is invalid.
    """
    try:
        # Validate inputs
        if len(labels) != len(values):
            raise ValueError("The number of labels must match the number of values.")
        orientation = orientation.lower()
        if orientation not in ("horizontal", "vertical"):
            raise ValueError("Orientation must be 'horizontal' or 'vertical'.")

        # Escape all user-provided text
        escaped_labels = [html.escape(str(label)) for label in labels]
        escaped_title = html.escape(str(title)) if title else None

        # Validate or default bar colors
        default_color = "#555555"
        safe_colors = []
        if bar_colors is None:
            bar_colors = []
        for i in range(len(values)):
            if i < len(bar_colors) and is_safe_color(bar_colors[i]):
                safe_colors.append(bar_colors[i])
            else:
                safe_colors.append(default_color)

        # Calculate scaling
        max_val = max(values) if values else 1
        if max_val == 0:
            max_val = 1  # Avoid division by zero

        # Chart dimensions
        chart_width = max(width, 50)
        chart_height = max(height, 50)
        title_height = 30 if title else 0
        bar_area_height = chart_height - title_height

        # Build HTML
        html_parts = [
            '<div class="bar-chart-container" style="',
            f'width:{chart_width}px;',
            f'height:{chart_height}px;',
            'font-family:sans-serif;',
            'border:1px solid #ccc;',
            'padding:4px;',
            'box-sizing:content-box;',
            'overflow:hidden;',
            '">'
        ]

        if escaped_title:
            html_parts.append(
                f'<div style="text-align:center;font-weight:bold;height:{title_height}px;line-height:{title_height}px;">'
                f'{escaped_title}</div>'
            )

        if orientation == "horizontal":
            # Each bar is a row: label | bar | value
            html_parts.append(
                f'<div style="display:flex;flex-direction:column;height:{bar_area_height}px;justify-content:space-around;">'
            )
            for label, val, color in zip(escaped_labels, values, safe_colors):
                pct = (val / max_val) * 100
                html_parts.append(
                    '<div style="display:flex;align-items:center;margin:2px 0;">'
                    f'<div style="width:60px;text-align:right;padding-right:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{label}</div>'
                    f'<div style="flex-grow:1;height:20px;background-color:{color};width:{pct:.1f}%;min-width:2px;"></div>'
                    f'<div style="padding-left:4px;white-space:nowrap;">{val}</div>'
                    '</div>'
                )
            html_parts.append('</div>')
        else:  # vertical
            # Each bar is a column: bar on top, label below, value below label
            html_parts.append(
                f'<div style="display:flex;align-items:flex-end;justify-content:space-around;height:{bar_area_height}px;padding-top:5px;">'
            )
            for label, val, color in zip(escaped_labels, values, safe_colors):
                bar_h = (val / max_val) * (bar_area_height - 40)  # reserve space for labels and values
                bar_h = max(bar_h, 2)
                html_parts.append(
                    '<div style="display:flex;flex-direction:column;align-items:center;">'
                    f'<div style="background-color:{color};width:30px;height:{bar_h:.0f}px;"></div>'
                    f'<div style="font-size:small;margin-top:2px;max-width:30px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{label}</div>'
                    f'<div style="font-size:small;margin-top:1px;">{val}</div>'
                    '</div>'
                )
            html_parts.append('</div>')

        html_parts.append('</div>')
        return ''.join(html_parts)

    except Exception as e:
        # Return a safe error message if anything unexpected occurs
        return f"Error generating chart: {html.escape(str(e))}"