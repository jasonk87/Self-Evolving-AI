# ai_assistant/code_synthesis/data_structures.py
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import uuid

class CodeTaskType(Enum):
    """
    Defines the different types of code tasks the system can handle.
    Aligns with contexts defined in unified_code_writing_system.md.
    """
    NEW_TOOL_CREATION_LLM = auto()
    EXISTING_TOOL_SELF_FIX_LLM = auto()
    EXISTING_TOOL_SELF_FIX_AST = auto()
    # Add more types as the system evolves

class CodeTaskStatus(Enum):
    """
    Defines the status of a code synthesis task.
    """
    PENDING = auto()
    IN_PROGRESS = auto()
    SUCCESS = auto()
    FAILURE_PRECONDITION = auto()      # e.g., missing required input
    FAILURE_LLM_GENERATION = auto()    # LLM failed to generate usable code
    FAILURE_CODE_APPLICATION = auto()  # e.g., AST modification failed, generated code invalid
    FAILURE_UNSUPPORTED_TASK = auto()
    NEEDS_REVIEW = auto()              # Code generated, but requires manual review
    PARTIAL_SUCCESS = auto()           # Some parts succeeded, others failed
    # Add more statuses as needed

@dataclass
class CodeTaskRequest:
    """
    Represents a request to the CodeSynthesisService.
    """
    task_type: CodeTaskType
    context_data: Dict[str, Any] # Payload specific to the task_type
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    llm_config_overrides: Optional[Dict[str, Any]] = None
    expected_output_format: Optional[str] = None # e.g., "python_function_string"
    # Add other generic fields if needed, e.g., priority, user_id

@dataclass
class CodeTaskResult:
    """
    Represents the result of a code synthesis task from CodeSynthesisService.
    """
    request_id: str # Corresponds to the CodeTaskRequest.request_id
    status: CodeTaskStatus
    generated_code: Optional[str] = None
    modified_code_path: Optional[str] = None # For modifications applied directly to files
    metadata: Optional[Dict[str, Any]] = field(default_factory=dict) # e.g., suggested func name, LLM logs
    error_message: Optional[str] = None
    review_comments: Optional[str] = None # Placeholder for future review integration
