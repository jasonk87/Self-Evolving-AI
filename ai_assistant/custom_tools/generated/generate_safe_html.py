import re
from typing import Optional

import bleach
from bleach.css_sanitizer import CSSSanitizer


_ALLOWED_TAGS = [
    "div", "span", "p", "br", "a", "b", "i", "strong", "em", "u",
    "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "table",
    "thead", "tbody", "tr", "th", "td", "img", "button", "hr", "pre",
    "code", "blockquote",
]

_ALLOWED_ATTRS = {
    "*": ["class", "style", "id", "title", "data-toggle", "data-target"],
    "a": ["href", "target", "rel"],
    "img": ["src", "alt", "width", "height"],
    "button": ["type", "disabled"],
}

_ALLOWED_PROTOCOLS = ["http", "https", "mailto", "data"]

_CSS_SANITIZER = CSSSanitizer()

_SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)


def generate_safe_html(input_string: Optional[str]) -> Optional[str]:
    """Sanitize HTML while preserving a safe subset of tags/attributes.

    Returns None for None input, to match existing tests.
    """
    if input_string is None:
        return None
    if not isinstance(input_string, str):
        return ""

    try:
        without_scripts = _SCRIPT_BLOCK_RE.sub("", input_string)
        safe_html = bleach.clean(
            without_scripts,
            tags=_ALLOWED_TAGS,
            attributes=_ALLOWED_ATTRS,
            protocols=_ALLOWED_PROTOCOLS,
            strip=True,
            css_sanitizer=_CSS_SANITIZER,
        )

        # Enforce rel for target=_blank links to prevent tabnabbing.
        safe_html = safe_html.replace('target="_blank">', 'target="_blank" rel="noopener noreferrer">')
        return safe_html
    except Exception:
        return ""
