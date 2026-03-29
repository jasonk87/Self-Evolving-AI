from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class BaseActionRequest(BaseModel):
    """
    Foundational data model for standardizing tool inputs.

    All incoming requests to tools must inherit from this model or match
    its signature. This ensures strict gating where malformed payloads
    are caught and rejected proactively before tool logic starts.
    """

    action_name: str = Field(
        ...,
        description="The unique identifier or command name of the action to be performed.",
    )

    parameters: Union[BaseModel, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Arguments required for the execution of the specified action. Can be a validated Pydantic model.",
    )


class BaseActionResponse(BaseModel):
    """
    Foundational data model for standardizing tool outputs.

    All tools must return results in a format conforming to this schema.
    This provides self-evolving agents with predictable structure to
    parse success states, read return data, and handle errors correctly.
    """

    success: bool = Field(
        ...,
        description="Boolean indicating whether the tool executed successfully without unhandled errors.",
    )

    result: Optional[Any] = Field(
        default=None,
        description="The main output payload or successful response value from the tool execution.",
    )

    error_message: Optional[str] = Field(
        default=None,
        description="Details regarding any failure or exception encountered. Populated if 'success' is False.",
    )
