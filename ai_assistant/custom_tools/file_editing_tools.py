
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

logger = logging.getLogger(__name__)

# --- Tool Wrappers for Self-Modification Functions ---

SCHEMA_PROPOSE_FUNCTION_MODIFICATION = {
    "name": "propose_function_modification",
    "description": "Proposes a modification to a specific Python function source code. Triggers a critical review process.",
    "parameters": [
        {
            "name": "module_path",
            "type": "str",
            "description": "The dotted module path (e.g., 'ai_assistant.custom_tools.my_tool')."
        },
        {
            "name": "function_name",
            "type": "str",
            "description": "The name of the function to modify."
        },
        {
            "name": "new_code_string",
            "type": "str",
            "description": "The complete, new source code for the function."
        },
        {
            "name": "change_description",
            "type": "str",
            "description": "Explanation of the change for the reviewer."
        }
    ]
}

async def propose_function_modification(module_path: str, function_name: str, new_code_string: str, change_description: str) -> Dict[str, Any]:
    """
    Wrapper for edit_function_source_code to be exposed as a tool.
    """
    # Assuming project root is current working directory for now
    project_root = os.getcwd()

    try:
        result_msg = await edit_function_source_code(
            module_path=module_path,
            function_name=function_name,
            new_code_string=new_code_string,
            project_root_path=project_root,
            change_description=change_description
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

SCHEMA_PROPOSE_CLASS_METHOD_MODIFICATION = {
    "name": "propose_class_method_modification",
    "description": "Proposes a modification to a method within a Python class.",
    "parameters": [
        {
            "name": "module_path",
            "type": "str",
            "description": "The module path containing the class."
        },
        {
            "name": "class_name",
            "type": "str",
            "description": "The name of the class."
        },
        {
            "name": "method_name",
            "type": "str",
            "description": "The name of the method to modify."
        },
        {
            "name": "new_code",
            "type": "str",
            "description": "The new method code."
        },
        {
            "name": "change_description",
            "type": "str",
            "description": "Description of the change."
        }
    ]
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

SCHEMA_UPSERT_IMPORT = {
    "name": "upsert_import",
    "description": "Ensures a specific import statement exists in a Python module.",
    "parameters": [
        {
            "name": "module_path",
            "type": "str",
            "description": "The target module path."
        },
        {
            "name": "import_statement",
            "type": "str",
            "description": "The full import statement (e.g., 'from typing import List')."
        },
        {
            "name": "change_description",
            "type": "str",
            "description": "Reason for adding the import."
        }
    ]
}

async def upsert_import(module_path: str, import_statement: str, change_description: str) -> Dict[str, Any]:
    # Wrapper function name in schema matches exposed name, but implementation can call core
    project_root = os.getcwd()
    try:
        # Note: Core function is also named upsert_import, importing as core_upsert_import would be cleaner but let's use the module function
        from ai_assistant.core.self_modification import upsert_import as core_upsert_import
        result_msg = await core_upsert_import(
            module_path=module_path,
            import_statement=import_statement,
            project_root_path=project_root,
            change_description=change_description
        )
        return {"status": "success", "message": result_msg}
    except Exception as e:
        return {"status": "error", "message": str(e)}

SCHEMA_INSERT_CODE_BLOCK = {
    "name": "insert_code_block",
    "description": "Inserts a block of code before or after a specific anchor string in a file.",
    "parameters": [
        {
            "name": "module_path",
            "type": "str",
            "description": "The file or module path."
        },
        {
            "name": "anchor_code",
            "type": "str",
            "description": "The existing code string to locate."
        },
        {
            "name": "new_code",
            "type": "str",
            "description": "The code to insert."
        },
        {
            "name": "position",
            "type": "str",
            "description": "'before' or 'after'."
        },
        {
            "name": "change_description",
            "type": "str",
            "description": "Reason for insertion."
        }
    ]
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

SCHEMA_SURGICAL_EDIT_FUNCTION = {
    "name": "surgical_edit_function",
    "description": "Performs a targeted replacement of a specific statement or block within a function using AST matching.",
    "parameters": [
        {
            "name": "module_path",
            "type": "str",
            "description": "Target module."
        },
        {
            "name": "function_name",
            "type": "str",
            "description": "Target function name."
        },
        {
            "name": "target_node_pattern",
            "type": "str",
            "description": "Code string representing the statement to find (e.g., 'return True')."
        },
        {
            "name": "replacement_code",
            "type": "str",
            "description": "The code to replace it with."
        },
        {
            "name": "change_description",
            "type": "str",
            "description": "Reason for change."
        }
    ]
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
