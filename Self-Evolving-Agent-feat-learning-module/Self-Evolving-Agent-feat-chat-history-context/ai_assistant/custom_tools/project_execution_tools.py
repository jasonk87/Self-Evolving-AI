### START FILE: ai_assistant/custom_tools/project_execution_tools.py ###
# ai_assistant/custom_tools/project_execution_tools.py
import json
import os
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

# Import BASE_PROJECTS_DIR and alias it for default usage
from ai_assistant.custom_tools.file_system_tools import (
    sanitize_project_name, 
    BASE_PROJECTS_DIR as DEFAULT_BASE_PROJECTS_DIR, # Alias for default
    read_text_from_file,
    write_text_to_file 
)
# Assuming generate_code_for_project_file is in project_management_tools as per your structure
from ai_assistant.custom_tools.project_management_tools import (
    generate_code_for_project_file,
    initiate_ai_project # Added import
)

# --- Plan Execution Tool ---

async def execute_project_coding_plan(project_name: str, base_projects_dir_override: Optional[str] = None) -> str:
    """
    Executes the coding plan for a given project by generating code for all
    tasks currently in a 'planned' state in the project manifest.

    Args:
        project_name: The name of the project whose coding plan is to be executed.
        base_projects_dir_override (Optional[str]): If provided, overrides the default base projects directory.

    Returns:
        A string summarizing the outcome of the code generation attempts for each
        planned file, including successes, failures, and skipped files.
    """
    if not project_name or not isinstance(project_name, str) or not project_name.strip():
        return "Error: Project name must be a non-empty string."

    successful_generations: List[str] = []
    failed_generations: List[str] = []
    skipped_files: List[str] = []
    files_processed_in_this_run_count = 0
    
    current_base_projects_dir = base_projects_dir_override if base_projects_dir_override is not None else DEFAULT_BASE_PROJECTS_DIR

    sanitized_name = sanitize_project_name(project_name) 
    project_dir = os.path.join(current_base_projects_dir, sanitized_name) 
    manifest_filepath = os.path.join(project_dir, "_ai_project_manifest.json")

    manifest_json_str = read_text_from_file(manifest_filepath)
    if manifest_json_str.startswith("Error:"):
        # If manifest is not found, try to initiate the project
        if "not found" in manifest_json_str.lower():
            print(f"Info: Manifest for project '{project_name}' not found. Attempting to auto-initiate project.")
            auto_description = f"Project '{project_name}' automatically initiated as it did not exist prior to plan execution attempt. User's original intent might provide more specific goals."
            init_result = await initiate_ai_project(project_name, auto_description)
            if init_result.startswith("Error:") or init_result.startswith("Warning:"):
                return f"Error: Failed to auto-initiate missing project '{project_name}'. Initiation tool said: {init_result}"
            print(f"Info: Project '{project_name}' auto-initiated successfully. {init_result}. Proceeding to execute plan.")
            # Re-read manifest after initiation
            manifest_json_str = read_text_from_file(manifest_filepath)
            if manifest_json_str.startswith("Error:"):
                return f"Error: Could not read manifest for '{project_name}' even after auto-initiation. {manifest_json_str}"
        else:
            return f"Error: Could not read project manifest for '{project_name}'. {manifest_json_str}"

    try:
        manifest_data = json.loads(manifest_json_str)
    except json.JSONDecodeError:
        return f"Error: Invalid project manifest format for project '{project_name}'."

    # MODIFIED: Use "development_tasks" key instead of "project_plan"
    development_tasks = manifest_data.get("development_tasks", [])
    if not development_tasks:
        return f"Info: Project plan (development_tasks) for '{project_name}' is empty or missing. Nothing to execute."

    # Iterate using the correct variable
    for task_entry in list(development_tasks): 
        if not isinstance(task_entry, dict): # Add a check
            print(f"Warning: Skipping malformed task entry in manifest for '{project_name}': {task_entry}")
            continue
            
        task_details = task_entry.get("details", {})
        filename = task_details.get("filename") # Filename is inside 'details' for CREATE_FILE tasks
        status = task_entry.get("status")
        task_type = task_entry.get("task_type")
        task_id = task_entry.get("task_id", "Unknown Task ID")


        if task_type != "CREATE_FILE" or not filename:
            # Log skipped non-CREATE_FILE tasks or tasks missing filenames appropriately
            if task_type != "CREATE_FILE":
                 print(f"Info: Skipping task '{task_id}' as it's not a CREATE_FILE task (type: {task_type}).")
            else: 
                 print(f"Warning: Skipping malformed CREATE_FILE task '{task_id}' for '{project_name}' (missing filename in details): {task_entry}")
            skipped_files.append(f"{filename or task_id} (type: {task_type or 'Unknown'}, status: {status or 'Unknown'})")
            continue

        if status == "planned":
            files_processed_in_this_run_count += 1
            print(f"Info: Attempting to generate code for '{filename}' (Task ID: {task_id}) in project '{project_name}'...")
            
            # generate_code_for_project_file is imported.
            # It's responsible for reading the manifest again to get detailed context for the specific file.
            generation_result_str = await generate_code_for_project_file(project_name, filename) 
            
            if generation_result_str.startswith("Success:"):
                successful_generations.append(f"{filename} (Task ID: {task_id}): Generation reported success.")
                # Note: generate_code_for_project_file is responsible for updating this task's status in the manifest
            else:
                failed_generations.append(f"{filename} (Task ID: {task_id}): {generation_result_str}")
                # Note: generate_code_for_project_file should ideally mark the task as 'failed' in the manifest
        else:
            skipped_files.append(f"{filename} (Task ID: {task_id}, status: {status})")
    
    if files_processed_in_this_run_count > 0 or successful_generations or failed_generations:
        # Re-read manifest to get its latest state (potentially modified by generate_code_for_project_file)
        current_manifest_content_for_timestamp_update = read_text_from_file(manifest_filepath) 
        if not current_manifest_content_for_timestamp_update.startswith("Error:"):
            try:
                final_manifest_data = json.loads(current_manifest_content_for_timestamp_update)
                final_manifest_data["last_modified_timestamp"] = datetime.now(timezone.utc).isoformat()
                write_result = write_text_to_file(manifest_filepath, json.dumps(final_manifest_data, indent=4))
                if write_result.startswith("Error:"): # pragma: no cover
                    print(f"Warning: Failed to update manifest's last_modified_timestamp for project '{project_name}' after plan execution: {write_result}")
            except json.JSONDecodeError: # pragma: no cover
                print(f"Warning: Could not parse manifest for final timestamp update in project '{project_name}' after plan execution.")
        else: # pragma: no cover
             print(f"Warning: Could not re-read manifest for final timestamp update in project '{project_name}' after plan execution: {current_manifest_content_for_timestamp_update}")

    if files_processed_in_this_run_count == 0 and not successful_generations and not failed_generations:
        report_lines_final = [
            f"Info: No files in 'planned' state found for project '{project_name}'. Nothing to do."
        ]
        if skipped_files:
            report_lines_final.append("\nFiles not processed (e.g., already generated, errored, or not CREATE_FILE type):")
            for skipped_info in skipped_files:
                report_lines_final.append(f"  - {skipped_info}")
        return "\n".join(report_lines_final)

    report_lines = [f"Code generation summary for project '{project_name}':"]
    
    if successful_generations:
        report_lines.append("\nSuccessfully generated code for:")
        for success_info in successful_generations:
            report_lines.append(f"  - {success_info}")
    
    if failed_generations:
        report_lines.append("\nFailed to generate code for:")
        for fail_info in failed_generations:
            report_lines.append(f"  - {fail_info}")
            
    if skipped_files: 
        report_lines.append("\nSkipped (not in 'planned' state, already processed, or not CREATE_FILE type):")
        for skipped_info in skipped_files:
            report_lines.append(f"  - {skipped_info}")
            
    report_lines.append(f"\nSummary: {len(successful_generations)} of {files_processed_in_this_run_count} attempted CREATE_FILE tasks generated successfully.")
    
    return "\n".join(report_lines)

# --- New Code Generation and Review Tool (as provided in your original file) ---
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async
from ai_assistant.config import get_model_for_task
from ai_assistant.core.reviewer import ReviewerAgent
from ai_assistant.core.refinement import RefinementAgent 
import re 

GENERAL_CODE_GENERATION_PROMPT_TEMPLATE = """
You are an AI assistant tasked with generating Python code.
Description of desired code:
{description}

Target file path (optional, for context): {target_file_path}
Existing code context (optional, from the target file or related files):
{existing_code_context}

Please generate the Python code that fulfills the description.
Respond ONLY with the raw Python code. Do not include any other explanations or markdown formatting like ```python.
"""

async def generate_and_review_code_tool(
    description: str,
    target_file_path: Optional[str] = None,
    existing_code_context: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generates Python code based on a description, then reviews it automatically.
    Includes iterative refinement based on review feedback.

    Args:
        description: Detailed description of the desired code functionality.
        target_file_path: (Optional) The intended file path for the generated code (for context).
        existing_code_context: (Optional) Snippets of existing code for context.

    Returns:
        A dictionary containing the generated code, review results,
        suggested file path, and overall status.
    """
    if not description:
        return {
            "generated_code": None,
            "review_results": {"status": "error", "comments": "Error: Description must be provided.", "suggestions": ""},
            "suggested_file_path": target_file_path,
            "status": "error"
        }

    # 1. Generate Code
    prompt = GENERAL_CODE_GENERATION_PROMPT_TEMPLATE.format(
        description=description,
        target_file_path=target_file_path or "Not specified",
        existing_code_context=existing_code_context or "Not provided"
    )
    
    code_gen_model = get_model_for_task("code_generation") 
    raw_generated_code = await invoke_ollama_model_async(prompt, model_name=code_gen_model, temperature=0.5, max_tokens=2000)

    if not raw_generated_code or not raw_generated_code.strip():
        return {
            "generated_code": None,
            "review_results": {"status": "error", "comments": "LLM failed to generate code.", "suggestions": ""},
            "suggested_file_path": target_file_path,
            "status": "error"
        }

    cleaned_code = re.sub(r"^\s*```python\s*\n?", "", raw_generated_code, flags=re.IGNORECASE)
    cleaned_code = re.sub(r"\n?\s*```\s*$", "", cleaned_code, flags=re.IGNORECASE).strip()

    if not cleaned_code:
        return {
            "generated_code": None,
            "review_results": {"status": "error", "comments": "LLM generated empty code after cleaning.", "suggestions": ""},
            "suggested_file_path": target_file_path,
            "status": "error"
        }

    current_generated_code = cleaned_code
    current_review_results: Dict[str, Any]
    reviewer = ReviewerAgent()
    refinement_agent = RefinementAgent()
    max_refinement_attempts = 2 

    for attempt in range(max_refinement_attempts + 1): 
        review_attempt_number = attempt + 1
        print(f"generate_and_review_code_tool: Reviewing code (Attempt {review_attempt_number})...")
        try:
            current_review_results = await reviewer.review_code(
                code_to_review=current_generated_code,
                original_requirements=description,
                attempt_number=review_attempt_number
            )
        except Exception as e: # pragma: no cover
            current_review_results = {
                "status": "error",
                "comments": f"Error during code review (Attempt {review_attempt_number}): {e}",
                "suggestions": ""
            }
            break 

        review_status = current_review_results.get("status", "error")
        print(f"generate_and_review_code_tool: Review Status (Attempt {review_attempt_number}): {review_status.upper()}")

        if review_status == "approved" or review_status == "rejected" or review_status == "error":
            break 

        if review_status == "requires_changes" and attempt < max_refinement_attempts:
            print(f"generate_and_review_code_tool: Code requires changes. Attempting refinement {attempt + 1}/{max_refinement_attempts}...")
            refined_code_str = await refinement_agent.refine_code(
                original_code=current_generated_code,
                requirements=description,
                review_feedback=current_review_results
            )
            if not refined_code_str or not refined_code_str.strip(): # pragma: no cover
                print(f"generate_and_review_code_tool: Refinement attempt {attempt + 1} did not produce new code. Using previous code.")
                break 
            current_generated_code = refined_code_str
        elif attempt >= max_refinement_attempts : # pragma: no cover
             print(f"generate_and_review_code_tool: Max refinement attempts reached. Using last reviewed code.")
             break

    return {
        "generated_code": current_generated_code,
        "review_results": current_review_results,
        "suggested_file_path": target_file_path,
        "status": current_review_results.get("status", "error") 
    }

if __name__ == '__main__':
    import asyncio
    import unittest
    from unittest.mock import patch, AsyncMock 
    import shutil 

    TEST_BASE_PROJECTS_DIR_FOR_DI = "temp_test_ai_projects_execution_di_final_v5" 

    def mock_sanitize_project_name_for_tests(name): 
        return "".join(c if c.isalnum() or c in (' ', '_', '-') else '_' for c in name).strip().replace(' ', '_').lower()

    class TestExecuteProjectCodingPlan(unittest.TestCase):
        
        TEST_BASE_DIR = TEST_BASE_PROJECTS_DIR_FOR_DI 

        @classmethod
        def setUpClass(cls):
            cls.TEST_BASE_DIR = TEST_BASE_PROJECTS_DIR_FOR_DI 
            if os.path.exists(cls.TEST_BASE_DIR): 
                shutil.rmtree(cls.TEST_BASE_DIR)
            os.makedirs(cls.TEST_BASE_DIR, exist_ok=True)

        @classmethod
        def tearDownClass(cls):
            if os.path.exists(cls.TEST_BASE_DIR):
                shutil.rmtree(cls.TEST_BASE_DIR)
        
        def _get_expected_manifest_path(self, project_name): 
            sanitized_name = mock_sanitize_project_name_for_tests(project_name)
            return os.path.join(self.TEST_BASE_DIR, sanitized_name, "_ai_project_manifest.json")

        def _create_mock_manifest_data(self, project_name, development_tasks_entries): 
            sanitized_proj_name = mock_sanitize_project_name_for_tests(project_name)
            project_dir = os.path.join(self.TEST_BASE_DIR, sanitized_proj_name)
            os.makedirs(project_dir, exist_ok=True) 
            
            return {
                "project_name": project_name,
                "sanitized_project_name": sanitized_proj_name,
                "project_directory": project_dir, 
                "project_description": "Test description",
                "creation_timestamp": datetime.now(timezone.utc).isoformat(),
                "last_modified_timestamp": datetime.now(timezone.utc).isoformat(),
                "version": "0.1.0",
                "manifest_version": "1.1.0",
                "project_type": "python",
                "entry_points": {},
                "dependencies": [],
                "build_config": {"build_command": None, "output_directory": None, "source_directories": ["src"]},
                "test_config": {"test_command": None, "test_directory": "tests"},
                "project_goals": [],
                "development_tasks": development_tasks_entries, 
                "project_notes": None
            }

        @patch('ai_assistant.custom_tools.project_execution_tools.sanitize_project_name', side_effect=mock_sanitize_project_name_for_tests)
        @patch('ai_assistant.custom_tools.project_execution_tools.read_text_from_file')
        @patch('ai_assistant.custom_tools.project_execution_tools.write_text_to_file')
        @patch('ai_assistant.custom_tools.project_execution_tools.generate_code_for_project_file', new_callable=AsyncMock)
        def test_one_file_planned_success(self, mock_gen_code, mock_write_file, mock_read_file, mock_sanitize_name_unused):
            project_name = "TestOneFileDI"
            manifest_data = self._create_mock_manifest_data(project_name, [
                {"task_id": "T001", "task_type": "CREATE_FILE", "description": "Main file task", 
                 "details": {"filename": "main.py", "original_description": "Main app logic"}, 
                 "status": "planned", "dependencies": []}
            ])
            mock_read_file.return_value = json.dumps(manifest_data)
            mock_gen_code.return_value = "Success: Code for 'main.py' generated."
            # Simulate manifest being updated by generate_code_for_project_file
            updated_manifest_data = self._create_mock_manifest_data(project_name, [
                {"task_id": "T001", "task_type": "CREATE_FILE", "description": "Main file task", 
                 "details": {"filename": "main.py", "original_description": "Main app logic"}, 
                 "status": "generated", "dependencies": [], "last_attempt_timestamp": datetime.now(timezone.utc).isoformat()}
            ])
            # Mock read_text_from_file to return updated manifest for the timestamp update part
            mock_read_file.side_effect = [json.dumps(manifest_data), json.dumps(updated_manifest_data)]
            mock_write_file.return_value = "Success: Manifest updated."

            result = asyncio.run(execute_project_coding_plan(project_name, base_projects_dir_override=self.TEST_BASE_DIR))
            
            mock_gen_code.assert_called_once_with(project_name, "main.py")
            self.assertIn("Successfully generated code for:", result)
            self.assertIn("main.py (Task ID: T001): Generation reported success.", result)
            self.assertIn("Summary: 1 of 1 attempted CREATE_FILE tasks generated successfully.", result)
            # Check if write_text_to_file was called to update the timestamp
            self.assertTrue(mock_write_file.called)


        @patch('ai_assistant.custom_tools.project_execution_tools.sanitize_project_name', side_effect=mock_sanitize_project_name_for_tests)
        @patch('ai_assistant.custom_tools.project_execution_tools.read_text_from_file')
        @patch('ai_assistant.custom_tools.project_execution_tools.write_text_to_file')
        @patch('ai_assistant.custom_tools.project_execution_tools.generate_code_for_project_file', new_callable=AsyncMock)
        def test_all_files_generated_nothing_to_do(self, mock_gen_code, mock_write_file, mock_read_file, mock_sanitize_name_unused):
            project_name = "TestAllGeneratedDI"
            manifest_data = self._create_mock_manifest_data(project_name, [
                 {"task_id": "T001", "task_type": "CREATE_FILE", "description": "Main file", 
                  "details": {"filename": "main.py"}, "status": "generated", "dependencies": []},
                 {"task_id": "T002", "task_type": "CREATE_FILE", "description": "Utils file", 
                  "details": {"filename": "utils.py"}, "status": "generated", "dependencies": []}
            ])
            mock_read_file.return_value = json.dumps(manifest_data)

            result = asyncio.run(execute_project_coding_plan(project_name, base_projects_dir_override=self.TEST_BASE_DIR))

            mock_gen_code.assert_not_called()
            self.assertIn("Info: No files in 'planned' state found", result)
            self.assertIn("Skipped (not in 'planned' state", result) 
            self.assertIn("main.py (Task ID: T001, status: generated)", result)
            mock_write_file.assert_not_called()

        @patch('ai_assistant.custom_tools.project_execution_tools.sanitize_project_name', side_effect=mock_sanitize_project_name_for_tests)
        @patch('ai_assistant.custom_tools.project_execution_tools.read_text_from_file')
        @patch('ai_assistant.custom_tools.project_execution_tools.write_text_to_file')
        @patch('ai_assistant.custom_tools.project_execution_tools.generate_code_for_project_file', new_callable=AsyncMock)
        def test_one_file_planned_generation_fails(self, mock_gen_code, mock_write_file, mock_read_file, mock_sanitize_name_unused):
            project_name = "TestOneFileFailDI"
            manifest_data = self._create_mock_manifest_data(project_name, [
                 {"task_id": "T001", "task_type": "CREATE_FILE", "description": "App file", 
                  "details": {"filename": "app.py"}, "status": "planned", "dependencies": []}
            ])
            mock_read_file.return_value = json.dumps(manifest_data)
            mock_gen_code.return_value = "Error: LLM failed for app.py"
            
            # Simulate manifest being updated by generate_code_for_project_file to mark as failed
            updated_manifest_data = self._create_mock_manifest_data(project_name, [
                {"task_id": "T001", "task_type": "CREATE_FILE", "description": "App file", 
                 "details": {"filename": "app.py"}, "status": "failed", "dependencies": [], 
                 "error_message": "LLM failed for app.py", "last_attempt_timestamp": datetime.now(timezone.utc).isoformat()}
            ])
            mock_read_file.side_effect = [json.dumps(manifest_data), json.dumps(updated_manifest_data)]
            mock_write_file.return_value = "Success: Manifest updated."


            result = asyncio.run(execute_project_coding_plan(project_name, base_projects_dir_override=self.TEST_BASE_DIR))

            mock_gen_code.assert_called_once_with(project_name, "app.py")
            self.assertIn("Failed to generate code for:", result)
            self.assertIn("app.py (Task ID: T001): Error: LLM failed for app.py", result)
            self.assertIn("Summary: 0 of 1 attempted CREATE_FILE tasks generated successfully.", result)
            self.assertTrue(mock_write_file.called)


        @patch('ai_assistant.custom_tools.project_execution_tools.sanitize_project_name', side_effect=mock_sanitize_project_name_for_tests)
        @patch('ai_assistant.custom_tools.project_execution_tools.read_text_from_file')
        @patch('ai_assistant.custom_tools.project_execution_tools.generate_code_for_project_file', new_callable=AsyncMock) 
        def test_missing_manifest(self, mock_gen_code, mock_read_file, mock_sanitize_name_unused):
            project_name = "TestMissingManifestDI"
            expected_manifest_path = self._get_expected_manifest_path(project_name)
            mock_read_file.return_value = f"Error: File '{expected_manifest_path}' not found."
            
            result = asyncio.run(execute_project_coding_plan(project_name, base_projects_dir_override=self.TEST_BASE_DIR))
            
            self.assertIn(f"Error: Could not read project manifest for '{project_name}'. Error: File '{expected_manifest_path}' not found.", result)
            mock_gen_code.assert_not_called()

        @patch('ai_assistant.custom_tools.project_execution_tools.sanitize_project_name', side_effect=mock_sanitize_project_name_for_tests)
        @patch('ai_assistant.custom_tools.project_execution_tools.read_text_from_file')
        @patch('ai_assistant.custom_tools.project_execution_tools.write_text_to_file')
        @patch('ai_assistant.custom_tools.project_execution_tools.generate_code_for_project_file', new_callable=AsyncMock)
        def test_empty_project_plan(self, mock_gen_code, mock_write_file, mock_read_file, mock_sanitize_name_unused):
            project_name = "TestEmptyPlanDI"
            # Pass an empty list for development_tasks
            manifest_data = self._create_mock_manifest_data(project_name, []) 
            mock_read_file.return_value = json.dumps(manifest_data)

            result = asyncio.run(execute_project_coding_plan(project_name, base_projects_dir_override=self.TEST_BASE_DIR))
            self.assertIn(f"Info: Project plan (development_tasks) for '{project_name}' is empty or missing. Nothing to execute.", result)
            mock_gen_code.assert_not_called()
            mock_write_file.assert_not_called() # Timestamp not updated if no files processed

    print("Running automated tests for execute_project_coding_plan (v5 - dependency injection)...")
    unittest.main(argv=['first-arg-is-ignored'], exit=False, verbosity=2)
### END FILE: ai_assistant/custom_tools/project_execution_tools.py ###