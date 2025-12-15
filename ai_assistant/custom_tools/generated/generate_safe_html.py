import bleach
import html

def generate_safe_html(input_string: str) -> str:
    """
    Generates safe HTML from a given input string by escaping and sanitizing it.

    Args:
        input_string: The string to convert to safe HTML.

    Returns:
        A string containing safe HTML.
    """
    if input_string is None:
        return ''
    allowed_tags = ['div', 'span', 'p', 'br', 'a', 'b', 'i', 'strong', 'em', 'u', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'img', 'button', 'hr', 'pre', 'code', 'blockquote']
    allowed_attrs = {'*': ['class', 'style', 'id', 'title', 'data-toggle', 'data-target'], 'a': ['href', 'target', 'rel'], 'img': ['src', 'alt', 'width', 'height'], 'button': ['type', 'disabled']}
    safe_html = bleach.clean(input_string, tags=allowed_tags, attributes=allowed_attrs, strip=True)
    return safe_html
if __name__ == '__main__':
    input_string = "<script>alert('XSS');</script><p style='color:red;'>Hello, world!</p>"
    safe_html = generate_safe_html(input_string)
    print(f'Original string: {input_string}')
    print(f'Safe HTML: {safe_html}')