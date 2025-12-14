import bleach
from typing import Optional

def generate_safe_html(html_content: str) -> Optional[str]:
    """
    Generates safe HTML content from a given HTML string. It uses the bleach library to sanitize the HTML,
    removing potentially malicious tags and attributes.

    Args:
        html_content (str): The HTML content to sanitize.

    Returns:
        Optional[str]: The sanitized HTML content, or None if an error occurred during sanitization.
    """
    try:
        # Define allowed tags and attributes for sanitization.  This is a conservative list.
        allowed_tags = bleach.ALLOWED_TAGS + ['p', 'br', 'strong', 'em', 'ul', 'ol', 'li', 'a', 'img', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'pre', 'code', 'blockquote']
        allowed_attributes = bleach.ALLOWED_ATTRIBUTES
        allowed_attributes['a'] = ['href', 'title', 'target']  # Allow target attribute for links
        allowed_attributes['img'] = ['src', 'alt', 'title'] # Allow image attributes
        allowed_protocols = bleach.ALLOWED_PROTOCOLS + ['data'] # Allow data urls for images

        sanitized_html = bleach.clean(html_content, tags=allowed_tags, attributes=allowed_attributes, protocols=allowed_protocols, strip=False)
        return sanitized_html
    except Exception as e:
        # Handle any potential errors during sanitization.
        print(f"Error sanitizing HTML: {e}")  # Consider using a logger here
        return None