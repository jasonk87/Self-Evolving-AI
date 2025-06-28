import uuid

# This dictionary is a placeholder for the actual in-memory store.
# In a real implementation, this store will be managed by the Orchestrator or ActionExecutor
# and passed to tools that need to interact with it, likely via a context object.
# For the purpose of defining the tool's interface and basic logic here,
# we use this module-level variable with a clear warning.
TEMP_LARGE_CONTENT_STORE_PLACEHOLDER = {}

SAVE_LARGE_CONTENT_SCHEMA = {
    "name": "save_large_content",
    "description": (
        "Stores large string content (e.g., HTML, code, long text) temporarily for the current plan execution "
        "and returns a placeholder reference string. Use this if you generate content that is expected to be very large "
        "(e.g., >10,000 characters as per current guidelines) and needs to be passed as an argument to another tool "
        "(like 'display_html_content_in_project_area'). The returned placeholder reference should then be used as the "
        "argument value for the subsequent tool."
    ),
    "parameters": [
        {
            "name": "content",
            "type": "string",
            "description": "The large string content to be stored.",
            "required": True
        },
        {
            "name": "suggested_id",
            "type": "string",
            "description": (
                "An optional suggested ID for the content. The system will ensure uniqueness for the session if provided, "
                "or generate a new unique ID if not. Primarily for LLM's internal tracking if needed."
            ),
            "required": False
        }
    ],
    "returns": {
        "type": "string",
        "description": "A placeholder reference string (e.g., '{{AI_CONTENT_REF::unique_id}}') that refers to the stored content."
    }
}

def save_large_content(content: str, suggested_id: str = None, execution_context: dict = None) -> str:
    """
    Stores large string content temporarily within the current execution context
    and returns a placeholder reference string.

    Args:
        content: The large string content to be stored.
        suggested_id: An optional suggested ID from the LLM.
        execution_context: A dictionary injected by the ActionExecutor, expected to contain
                           a 'large_content_store'.

    Returns:
        A placeholder reference string.

    Raises:
        ValueError: If the execution_context or large_content_store is not provided.
    """
    if execution_context is None or "large_content_store" not in execution_context:
        # This error indicates a system-level problem: ActionExecutor isn't providing context.
        # This tool should not fall back to a global store in production.
        raise ValueError("`save_large_content` requires 'large_content_store' in 'execution_context'.")

    actual_store = execution_context["large_content_store"]

    if not isinstance(actual_store, dict):
        raise ValueError("'large_content_store' in 'execution_context' must be a dictionary.")

    if suggested_id:
        placeholder_id = str(suggested_id)
        # Ensure uniqueness if a suggestion is provided
        base_id = placeholder_id
        counter = 0
        while placeholder_id in actual_store:
            counter += 1
            placeholder_id = f"{base_id}_{counter}"
            if counter > 1000: # Safety break for runaway loop
                 placeholder_id = uuid.uuid4().hex # Fallback to UUID
                 break
    else:
        placeholder_id = uuid.uuid4().hex

    actual_store[placeholder_id] = content

    # Format defined in placeholder mechanism design
    # Note: Double curly braces are for f-string literal curly braces
    placeholder_reference = f"{{{{AI_CONTENT_REF::{placeholder_id}}}}}"

    # Proper logging should be used here instead of print in a real system
    print(f"AI_TOOL_LOG: save_large_content: Stored content with ID '{placeholder_id}'. Length: {len(content)}. Returning ref: {placeholder_reference}")
    return placeholder_reference

# To make this tool discoverable, you might need an __init__.py in custom_tools
# and ensure this module is imported, or have a registration mechanism.
# For now, assuming the ToolSystem will find it if this file is in the search path.

# Example of how the ActionExecutor would need to prepare the context:
#
# class ActionExecutor:
#     def __init__(self):
#         self.current_plan_large_content_store = {} # Managed per plan
#
#     async def execute_step(self, step, ...):
#         # ...
#         tool_kwargs = resolved_kwargs
#         if tool_name == "save_large_content":
#             # Inject the store for this specific tool
#             tool_kwargs['execution_context'] = {"large_content_store": self.current_plan_large_content_store}
#
#         # When resolving args for ANY tool:
#         # new_args = []
#         # for arg in args:
#         #   if is_placeholder(arg): new_args.append(self.current_plan_large_content_store.get(extract_id(arg)))
#         #   else: new_args.append(arg)
#         # ... then call tool with new_args
#
# This interaction logic is key and will be part of Step 5 & 6.
#
# The schema should also be accessible to the ToolSystem.
# Usually, this is done by having a list of schemas or a discovery mechanism.
# For example, in __init__.py of content_management_tools:
#
# from .content_management_tools import SAVE_LARGE_CONTENT_SCHEMA, save_large_content
# TOOL_SCHEMAS = [SAVE_LARGE_CONTENT_SCHEMA]
# ALL_TOOLS = {"save_large_content": save_large_content}

__all__ = ["save_large_content", "SAVE_LARGE_CONTENT_SCHEMA"]
