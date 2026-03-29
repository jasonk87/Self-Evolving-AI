from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from enum import Enum

class ExecutionStatus(str, Enum):
    INITIALIZED = "initialized"
    PLANNING = "planning"
    TOOL_EXECUTION = "tool_execution"
    COMPLETED = "completed"
    FAILED = "failed"
    CODING = "coding"

class ToolExecutionRecord(BaseModel):
    action_name: str
    input_summary: Optional[Dict[str, Any]] = None
    success: bool
    result_summary: Optional[Any] = None
    error_message: Optional[str] = None
    timestamp: Optional[float] = None
    retry_count: Optional[int] = 0
    source_agent_or_cycle: Optional[str] = None




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

    working_prompt: str | None = Field(
        default=None,
        description="The effective prompt, which may be enriched with vision context or other system text.",
    )

    current_status: ExecutionStatus = Field(
        default=ExecutionStatus.INITIALIZED,
        description="Current system status or stage represented as an Enum.",
    )

    errors: List[str] = Field(
        default_factory=list,
        description="An append-only list of errors or validation failures encountered during this execution.",
    )

    tool_results: List[ToolExecutionRecord] = Field(
        default_factory=list,
        description="An append-only list tracking the outcomes of previously executed tools, explicitly typed for better tracking.",
    )

    context_limits: Dict[str, int] = Field(
        default_factory=dict,
        description="Tracks current context or memory limits (e.g., max tokens, available memory) to guide agent outputs.",
    )

    final_answer: str | None = Field(
        default=None,
        description="The final natural language response produced by the agent to be delivered to the user.",
    )

    final_images: List[str] = Field(
        default_factory=list,
        description="A list of file paths to images collected during execution to be returned to the user.",
    )
