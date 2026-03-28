
import os
import ast
import json
import logging
import asyncio
from typing import Optional, Dict, Any, List
from ai_assistant.core.self_modification import (
    edit_function_source_code,
    edit_class_method,
    upsert_import,
    insert_code_block,
    surgical_edit_function
)
from ai_assistant.custom_tools.file_system_tools import read_text_from_file, write_text_to_file
from ai_assistant.core.task_manager import TaskManager
import shutil
import datetime
from typing import List, Dict, Union

logger = logging.getLogger(__name__)

from pydantic import BaseModel, Field
from typing import List, Optional

class EditInstruction(BaseModel):
    start: int = Field(..., description="Inclusive 1-based start line number.")
    end: int = Field(..., description="Inclusive 1-based end line number.")
    content: str = Field(..., description="The new content to replace those lines.")

class ModifyFileLinesSchema(BaseModel):
    file_path: str = Field(..., description="The absolute path to the file to modify.")
    edits: List[EditInstruction] = Field(..., description="A list of edit dictionaries, each with 'start' (int), 'end' (int), and 'content' (str).")
    backup: Optional[bool] = Field(True, description="Whether to create a backup before editing. Default True.")

SCHEMA_MODIFY_FILE_LINES = {
    "name": "modify_file_lines",
    "description": "Surgically modifies a file by replacing specific lines or ranges of lines.",
    "parameters": ModifyFileLinesSchema.model_json_schema()
}

def modify_file_lines(
    file_path: str,
    edits: List[Dict[str, Union[int, str]]],
    backup: bool = True,
    task_manager: Optional[TaskManager] = None,
    parent_task_id: Optional[str] = None
) -> str:
    """
    Surgically modifies a file by replacing specific lines or ranges of lines.

    Args:
        file_path (str): The absolute path to the file to modify.
        edits (List[Dict]): A list of edit specifications. Each edit is a dict with:
            - "start" (int): 1-indexed start line number.
            - "end" (int): 1-indexed end line number (inclusive).
            - "content" (str): The new content to insert. Can be multiple lines (newline separated).
        backup (bool): Whether to create a backup of the file before editing. Defaults to True.

    Returns:
        str: A message indicating success or failure.
    """
    if not os.path.exists(file_path):
        return f"Error: File not found at {file_path}"

    if not os.path.isfile(file_path):
        return f"Error: Path is not a file: {file_path}"

    try:
        # Validate edits format
        validated_edits = []
        for edit in edits:
            if not isinstance(edit, dict):
                return "Error: Each edit must be a dictionary."

            start = edit.get("start")
            end = edit.get("end")
            content = edit.get("content")

            if start is None or end is None or content is None:
                return "Error: Each edit must have 'start', 'end', and 'content' keys."

            if not isinstance(start, int) or not isinstance(end, int):
                return f"Error: 'start' and 'end' must be integers. Got start={start}, end={end}."

            if start < 1:
                return f"Error: Line numbers must be 1-indexed (start >= 1). Got {start}."

            if end < start:
                return f"Error: 'end' line must be >= 'start' line. Got start={start}, end={end}."

            if not isinstance(content, str):
                return f"Error: 'content' must be a string. Got {type(content)}."

            validated_edits.append({
                "start": start,
                "end": end,
                "content": content
            })

        # Create backup
        if backup:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{file_path}.{timestamp}.bak"
            shutil.copy2(file_path, backup_path)

        # Read file lines
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        total_lines = len(lines)

        # Sort edits by start line descending to avoid offset issues
        # and checking for overlaps
        validated_edits.sort(key=lambda x: x["start"], reverse=True)

        last_start = float('inf')

        for edit in validated_edits:
            start = edit["start"]
            end = edit["end"]
            content = edit["content"]

            # Check for overlaps (since we iterate backwards, current end must be < last_start)
            if end >= last_start:
                 # Overlap detected!
                 return f"Error: Overlapping edits detected. Please merge overlapping ranges. Conflict near line {end}."

            last_start = start

            # Check bounds
            if start > total_lines + 1:
                # Appending way past end
                return f"Error: Start line {start} is beyond end of file ({total_lines})."

            # Adjust indices for 0-based list
            start_idx = start - 1
            end_idx = end # split is exclusive at end, so line 5 (idx 4) to 5 means [4:5]

            # Normalize content to list of lines
            new_lines_list = content.splitlines(keepends=True)
            if content and not content.endswith('\n'):
                 if new_lines_list:
                    new_lines_list[-1] = new_lines_list[-1] + '\n'

            # Apply splice
            if start_idx > len(lines):
                 lines.extend(new_lines_list)
            else:
                lines[start_idx:end_idx] = new_lines_list

        # Write back
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)

        return f"Success: Applied {len(validated_edits)} edits to {os.path.basename(file_path)}."

    except Exception as e:
        return f"Error modifying file: {str(e)}"

# --- Tool Wrappers for Self-Modification Functions ---

class ProposeFunctionModificationSchema(BaseModel):
    module_path: str = Field(..., description="The dotted module path (e.g., 'ai_assistant.custom_tools.my_tool').")
    function_name: str = Field(..., description="The name of the function to modify.")
    new_code_string: str = Field(..., description="The complete, new source code for the function. DO NOT USE LAZY PLACEHOLDERS LIKE '# ... existing code ...'.")
    change_description: str = Field(..., description="Explanation of the change for the reviewer.")
    unit_test_code: str = Field(..., description="MANDATORY: Write an asserting `pytest` function to prove this modification works.")

SCHEMA_PROPOSE_FUNCTION_MODIFICATION = {
    "name": "propose_function_modification",
    "description": "Proposes a modification to a specific Python function source code. Triggers a critical review process.",
    "parameters": ProposeFunctionModificationSchema.model_json_schema()
}

async def propose_function_modification(module_path: str, function_name: str, new_code_string: str, change_description: str, unit_test_code: str) -> Dict[str, Any]:
    """
    Wrapper for edit_function_source_code to be exposed as a tool.
    Requires an associated unit test string.
    """
    # Assuming project root is current working directory for now
    project_root = os.getcwd()

    try:
        # Since edit_function_source_code doesn't natively accept unit_test_code yet,
        # we append the unit test to the change description so the Executor/Council sees it.
        enhanced_description = f"{change_description}\n\n[MANDATORY UNIT TEST]\n{unit_test_code}"

        result_msg = await edit_function_source_code(
            module_path=module_path,
            function_name=function_name,
            new_code_string=new_code_string,
            project_root_path=project_root,
            change_description=enhanced_description
        )

        status = "success" if "success" in result_msg.lower() else "error"
        # If it was rejected, status is technically success of the tool execution (it ran), but outcome is rejection
        if "rejected" in result_msg.lower():
            status = "rejected_by_review"

        return {
            "status": status,
            "message": result_msg
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"An unexpected error occurred: {e}"
        }

class ProposeClassMethodModificationSchema(BaseModel):
    module_path: str = Field(..., description="The module path containing the class.")
    class_name: str = Field(..., description="The name of the class.")
    method_name: str = Field(..., description="The name of the method to modify.")
    new_code: str = Field(..., description="The new method code.")
    change_description: str = Field(..., description="Description of the change.")

SCHEMA_PROPOSE_CLASS_METHOD_MODIFICATION = {
    "name": "propose_class_method_modification",
    "description": "Proposes a modification to a method within a Python class.",
    "parameters": ProposeClassMethodModificationSchema.model_json_schema()
}

async def propose_class_method_modification(module_path: str, class_name: str, method_name: str, new_code: str, change_description: str) -> Dict[str, Any]:
    project_root = os.getcwd()
    try:
        result_msg = await edit_class_method(
            module_path=module_path,
            class_name=class_name,
            method_name=method_name,
            new_code=new_code,
            project_root_path=project_root,
            change_description=change_description
        )
        status = "success" if "success" in result_msg.lower() else "error"
        if "rejected" in result_msg.lower(): status = "rejected_by_review"
        return {"status": status, "message": result_msg}
    except Exception as e:
        return {"status": "error", "message": str(e)}

class UpsertImportSchema(BaseModel):
    module_path: str = Field(..., description="The target module path.")
    import_statement: str = Field(..., description="The full import statement (e.g., 'from typing import List').")
    change_description: str = Field(..., description="Reason for adding the import.")

SCHEMA_UPSERT_IMPORT = {
    "name": "upsert_import",
    "description": "Ensures a specific import statement exists in a Python module.",
    "parameters": UpsertImportSchema.model_json_schema()
}

async def upsert_import(module_path: str, import_statement: str, change_description: str) -> Dict[str, Any]:
    project_root = os.getcwd()
    try:
        from ai_assistant.core.self_modification import upsert_import as core_upsert_import
        result_msg = await core_upsert_import(module_path=module_path, import_statement=import_statement, project_root_path=project_root, change_description=change_description)
        return {'status': 'success', 'message': result_msg}
    except ImportError as e:
        return {'status': 'error', 'message': f'Could not import upsert_import from ai_assistant.core.self_modification. Ensure ai_assistant package is installed and in PYTHONPATH. Original error: {str(e)}'}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

class InsertCodeBlockSchema(BaseModel):
    module_path: str = Field(..., description="The file or module path.")
    anchor_code: str = Field(..., description="The existing code string to locate.")
    new_code: str = Field(..., description="The code to insert.")
    position: str = Field(..., description="'before' or 'after'.")
    change_description: str = Field(..., description="Reason for insertion.")

SCHEMA_INSERT_CODE_BLOCK = {
    "name": "insert_code_block",
    "description": "Inserts a block of code before or after a specific anchor string in a file.",
    "parameters": InsertCodeBlockSchema.model_json_schema()
}

async def insert_code_block(module_path: str, anchor_code: str, new_code: str, position: str, change_description: str) -> Dict[str, Any]:
    project_root = os.getcwd()
    try:
        from ai_assistant.core.self_modification import insert_code_block as core_insert_code_block
        result_msg = await core_insert_code_block(
            module_path=module_path,
            anchor_code=anchor_code,
            new_code=new_code,
            position=position,
            project_root_path=project_root,
            change_description=change_description
        )
        return {"status": "success", "message": result_msg}
    except Exception as e:
        return {"status": "error", "message": str(e)}

class SurgicalEditFunctionSchema(BaseModel):
    module_path: str = Field(..., description="Target module.")
    function_name: str = Field(..., description="Target function name.")
    target_node_pattern: str = Field(..., description="Code string representing the statement to find (e.g., 'return True').")
    replacement_code: str = Field(..., description="The code to replace it with.")
    change_description: str = Field(..., description="Reason for change.")

SCHEMA_SURGICAL_EDIT_FUNCTION = {
    "name": "surgical_edit_function",
    "description": "Performs a targeted replacement of a specific statement or block within a function using AST matching.",
    "parameters": SurgicalEditFunctionSchema.model_json_schema()
}

async def surgical_edit_function(module_path: str, function_name: str, target_node_pattern: str, replacement_code: str, change_description: str) -> Dict[str, Any]:
    project_root = os.getcwd()
    try:
        from ai_assistant.core.self_modification import surgical_edit_function as core_surgical_edit_function
        result_msg = await core_surgical_edit_function(
            module_path=module_path,
            function_name=function_name,
            target_node_pattern=target_node_pattern,
            replacement_code=replacement_code,
            project_root_path=project_root,
            change_description=change_description
        )
        status = "success" if "success" in result_msg.lower() else "error"
        if "rejected" in result_msg.lower(): status = "rejected_by_review"
        return {"status": status, "message": result_msg}
    except Exception as e:
        return {"status": "error", "message": str(e)}
