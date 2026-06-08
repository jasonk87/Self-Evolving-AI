import uuid
from typing import Any, Dict, List
from pydantic import BaseModel, Field


class ExecutionState(BaseModel):
    """
    Unified execution state pipeline model.

    This object travels from the Controller -> Planner -> Coder -> Tools, tracking
    all necessary context, active status, and errors throughout an execution cycle.
    Self-evolving agents must use and update this state to maintain a consistent
    understanding of the workflow.
    """

    original_user_prompt: str = Field(
        ...,
        description="The original instruction or request provided by the user.",
    )

    current_status: str = Field(
        default="initialized",
        description="Current system status or stage. Examples: 'planning', 'coding', 'tool_execution', 'completed', 'failed'.",
    )

    errors: List[str] = Field(
        default_factory=list,
        description="An append-only list of errors or validation failures encountered during this execution.",
    )

    tool_results: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="An append-only list tracking the outcomes of previously executed tools.",
    )

    context_limits: Dict[str, int] = Field(
        default_factory=dict,
        description="Tracks current context or memory limits (e.g., max tokens, available memory) to guide agent outputs.",
    )

    correlation_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="A unique identifier for tracking this execution flow across systems.",
    )
