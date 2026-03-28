from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

class ProposeToolModificationAction(BaseModel):
    tool_name: str = Field(..., description="Name of the tool being modified.")
    suggested_code_change: str = Field(..., description="The suggested python code for the modification.")
    module_path: str = Field(..., description="The path to the python module containing the tool.")
    function_name: str = Field(..., description="The exact function name to modify.")
    suggested_change_description: str = Field("No specific description provided.", description="Description of the change.")

class ExecuteComplexProjectTaskAction(BaseModel):
    contract: Dict[str, Any] = Field(..., description="The SwarmContract dictionary payload to initialize a sub-swarm.")

class AddLearnedFactAction(BaseModel):
    fact_to_learn: str = Field(..., description="The specific factual statement to add to long-term memory.")

class ExecuteEphemeralAgentAction(BaseModel):
    task_description: str = Field(..., description="Description of the task to be handled by the ephemeral agent.")
    # Other parameters can be dynamically extracted by _execute_ephemeral_agent_task,
    # but strictly defining the mandatory field ensures safety.

class ExecuteSuggestedToolAction(BaseModel):
    tool_name: str = Field(..., description="The name of the tool to automatically execute (e.g. 'install_python_package').")
    args: Dict[str, Any] = Field(default_factory=dict, description="Keyword arguments for the tool execution.")

class ApplyArchitectProposalAction(BaseModel):
    target_file: str = Field(..., description="The absolute path of the file to modify.")
    proposal_summary: str = Field(..., description="A high-level summary of the architectural changes.")
    proposal_plan: str = Field(..., description="Detailed instructions on how to modify the file.")

class OperatorResponse(BaseModel):
    """
    The strictly typed response schema required from the Operator agent during the execution cycle.
    """
    thought: str = Field(..., description="Brief reasoning for this action.")
    type: str = Field(..., description="Must be exactly 'tool_call' or 'final_answer'.")
    name: Optional[str] = Field(None, description="The name of the tool, if type is 'tool_call'.")
    params: Dict[str, Any] = Field(default_factory=dict, description="Arguments for the tool, or {'message': '...'} if final_answer.")
