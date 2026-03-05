import logging

logger = logging.getLogger(__name__)

try:
    import tiktoken
    _encoder = tiktoken.get_encoding("cl100k_base")  # Good default for modern LLMs
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False
    logger.warning("tiktoken not installed. Using character-based heuristic for token estimation.")

def estimate_tokens(text: str) -> int:
    """
    Estimates the number of tokens in a given text string.
    Uses tiktoken if available, otherwise falls back to a 4 chars/token heuristic.
    """
    if not text:
        return 0

    if TIKTOKEN_AVAILABLE:
        try:
            return len(_encoder.encode(text))
        except Exception as e:
            logger.debug(f"tiktoken encoding failed: {e}. Falling back to heuristic.")

    # Fallback heuristic: roughly 4 characters per token
    return len(text) // 4

def truncate_to_token_limit(text: str, max_tokens: int) -> str:
    """
    Truncates a string from the beginning (keeping the end) to fit within a token limit.
    Useful for chat history where recent messages are more important.
    """
    if estimate_tokens(text) <= max_tokens:
        return text

    if TIKTOKEN_AVAILABLE:
        try:
            tokens = _encoder.encode(text)
            # Keep the last max_tokens
            truncated_tokens = tokens[-max_tokens:]
            return _encoder.decode(truncated_tokens)
        except Exception:
            pass

    # Fallback heuristic: keep the last N characters
    return text[-max_tokens * 4:]
