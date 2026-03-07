class BudgetExceededError(Exception):
    """Exception raised when an LLM call exceeds the allowed token or category budget."""
    def __init__(self, message="LLM budget exceeded."):
        super().__init__(message)
