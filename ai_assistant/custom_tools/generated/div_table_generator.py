from typing import List, Dict, Any


def _open_div(style: str) -> str:
    return f'<div style="{style}">' if style else '<div>'


def generate_div_table_html(data: List[List[Any]], styles: Dict[str, str] = None) -> str:
    """Generate div-based table HTML with optional table/row/cell style strings."""
    try:
        styles = styles or {}
        table_style = styles.get('table', '')
        row_style = styles.get('row', '')
        cell_style = styles.get('cell', '')

        html = _open_div(table_style)
        for row_data in data:
            html += _open_div(row_style)
            for cell_data in row_data:
                html += f"{_open_div(cell_style)}{cell_data}</div>"
            html += '</div>'
        html += '</div>'
        return html
    except Exception as e:
        return f"Error generating HTML: {e}"
