import os
import sys
import traceback # Added for better error reporting in generate_consolidated_file

def generate_project_structure_string(project_dir_abs_path: str, files_to_skip: set) -> str:
    """
    Generates a string representation of the project's directory structure.
    Skips specified files (like the script itself or the output file).
    """
    structure_lines = [f"Project Root Scanned: {project_dir_abs_path}\nStructure:"]
    paths_to_display = []

    for dirpath, dirnames, filenames in os.walk(project_dir_abs_path, topdown=True):
        # Modify dirnames in-place to exclude certain directories from os.walk
        # This is more efficient than checking inside the loop for skipped dirs
        dirs_to_skip_for_walk = {'.git', '.vscode', '.idea', '__pycache__', 'node_modules', 'venv', 'dist', 'build', 'target'}
        dirnames[:] = [d for d in dirnames if d not in dirs_to_skip_for_walk and not d.startswith('.')]
        dirnames.sort() # Sort here to affect os.walk order slightly, and for consistent display prep

        relative_dirpath = os.path.relpath(dirpath, project_dir_abs_path)
        depth = relative_dirpath.count(os.sep) if relative_dirpath != '.' else 0

        if relative_dirpath != '.': # Add current directory if not the root itself
             # Check if current dirpath itself should be skipped based on its name
            if os.path.basename(dirpath) in dirs_to_skip_for_walk or os.path.basename(dirpath).startswith('.'):
                continue # Skip processing this directory and its contents further for display if it matches skip criteria
            paths_to_display.append((depth, os.path.basename(dirpath), True, relative_dirpath))

        # Add files to display list
        for filename in sorted(filenames): # Sort for consistent order
            if filename in files_to_skip: # Skips script itself and output file
                continue
            full_file_path = os.path.join(dirpath, filename)
            relative_file_path = os.path.relpath(full_file_path, project_dir_abs_path)
            paths_to_display.append((depth + 1, filename, False, relative_file_path))

    # Sort paths: by full relative path for overall consistency.
    # Tree structure will be built based on depth.
    paths_to_display.sort(key=lambda item: item[3].lower()) # Sort by full relative path
    
    current_path_parts = []
    for depth, name, is_dir, relative_path in paths_to_display:
        # This simplified indentation doesn't draw perfect tree lines but is readable.
        indent = "  " * depth
        entry_type = "D" if is_dir else "F"
        structure_lines.append(f"{indent}├── [{entry_type}] {name}")

    if not paths_to_display and relative_dirpath == '.': # Only if root itself had no displayable content
        structure_lines.append("  (Project root appears to be empty or contains only skipped files/directories)")
        
    return "\n".join(structure_lines)


def generate_consolidated_file(project_dir, output_filename_abs, script_basename_to_ignore):
    """
    Recursively finds all .py files in project_dir (excluding the script itself
    and the output file), and concatenates their content into output_filename_abs,
    with comments indicating the original file path.
    Also includes the project structure at the top of the output file.
    """
    abs_project_dir = os.path.abspath(project_dir)
    # output_filename_abs is already absolute

    if not os.path.isdir(abs_project_dir):
        print(f"Error: Hardcoded Project directory '{project_dir}' (resolved to '{abs_project_dir}') not found or is not a directory.")
        print(f"Please check the 'HARDCODED_PROJECT_ROOT_DIR' variable in the script.")
        return

    print(f"Scanning project directory: {abs_project_dir}")
    print(f"Output will be written to (and overwritten if exists): {output_filename_abs}")
    print(f"Ignoring script file: {script_basename_to_ignore}")
    print(f"Ignoring output file name: {os.path.basename(output_filename_abs)}")

    files_to_skip_for_structure_and_content = {
        script_basename_to_ignore,
        os.path.basename(output_filename_abs) # Basename of the absolute output path
    }

    processed_files_count = 0
    skipped_content_files_count = 0

    try:
        print("Generating project structure...")
        project_structure_str = generate_project_structure_string(abs_project_dir, files_to_skip_for_structure_and_content)
        print("Project structure generation complete.")

        # 'w' mode overwrites the file if it exists
        with open(output_filename_abs, 'w', encoding='utf-8') as outfile:
            outfile.write("# === CONSOLIDATED PROJECT VIEW FOR AI ASSISTANT ===\n")
            outfile.write("# This file combines multiple Python files from a project into a single text file.\n")
            outfile.write("# Its purpose is to provide full contextual understanding to an AI.\n")
            outfile.write("#\n")
            outfile.write("# HOW TO READ THIS FILE (for AI):\n")
            outfile.write("# 1. PROJECT STRUCTURE: The section below outlines the original folder and file layout.\n")
            outfile.write("#    [D] denotes a directory, [F] denotes a file.\n")
            outfile.write("# 2. CODE BLOCKS: Each original file's content is enclosed by:\n")
            outfile.write("#    ### START FILE: path/to/your/file.py ###\n")
            outfile.write("#    (content of file.py)\n")
            outfile.write("#    ### END FILE: path/to/your/file.py ###\n")
            outfile.write("#    Use these markers to understand the original separation and context of the code.\n")
            outfile.write("# 3. EXECUTION: This consolidated file is NOT meant to be executed directly.\n")
            outfile.write("#    Imports and relative paths will likely be broken.\n")
            outfile.write("# =================================================\n\n")

            outfile.write("# --- PROJECT FILE STRUCTURE ---\n")
            outfile.write(project_structure_str)
            outfile.write("\n# --- END OF PROJECT FILE STRUCTURE ---\n\n\n")

            outfile.write(f"# --- CONSOLIDATED CODE CONTENT (from: {abs_project_dir}) ---\n")
            outfile.write(f"# Generated by script: {script_basename_to_ignore}\n") # Basename of the script
            outfile.write(f"# Output file name: {os.path.basename(output_filename_abs)}\n\n")

            all_py_files_to_process_content = []
            for dirpath, dirnames, filenames in os.walk(abs_project_dir, topdown=True):
                # Skip common non-code directories for content processing too
                dirs_to_skip_for_walk = {'.git', '.vscode', '.idea', '__pycache__', 'node_modules', 'venv', 'dist', 'build', 'target'}
                dirnames[:] = [d for d in dirnames if d not in dirs_to_skip_for_walk and not d.startswith('.')]

                for filename in filenames:
                    if filename.endswith(".py"):
                        full_file_path_abs = os.path.abspath(os.path.join(dirpath, filename))
                        
                        # Check if this is the script itself
                        # Note: __file__ is the path of the currently running script.
                        is_current_script = os.path.abspath(__file__) == full_file_path_abs
                        
                        if is_current_script:
                            print(f"Skipping content of the script itself: {full_file_path_abs}")
                            skipped_content_files_count += 1
                            continue
                        if full_file_path_abs == output_filename_abs: # Already absolute
                            print(f"Skipping content of the output file itself: {full_file_path_abs}")
                            skipped_content_files_count += 1
                            continue
                            
                        all_py_files_to_process_content.append(full_file_path_abs)
            
            all_py_files_to_process_content.sort()

            for file_path_abs in all_py_files_to_process_content:
                relative_path = os.path.relpath(file_path_abs, abs_project_dir)
                comment_path = relative_path.replace(os.sep, '/') # Normalize for comments

                outfile.write(f"# ### START FILE: {comment_path} ###\n")
                try:
                    with open(file_path_abs, 'r', encoding='utf-8') as infile:
                        outfile.write(infile.read())
                    outfile.write(f"\n# ### END FILE: {comment_path} ###\n\n")
                    print(f"Processed content of: {comment_path}")
                    processed_files_count += 1
                except Exception as e:
                    error_message = f"# !!! Error reading file {comment_path}: {e} !!!\n"
                    outfile.write(error_message)
                    outfile.write(f"# ### END FILE: {comment_path} ###\n\n") # Still provide end marker
                    print(f"Error processing content of file {comment_path}: {e}")
                    skipped_content_files_count += 1

        print(f"\nConsolidated file '{os.path.basename(output_filename_abs)}' created successfully in '{os.path.dirname(output_filename_abs)}'.")
        print(f"Total Python files processed for content: {processed_files_count}")
        print(f"Total Python files skipped for content (self, output, or errors): {skipped_content_files_count}")

    except IOError as e: # pragma: no cover
        print(f"IOError related to output file '{output_filename_abs}': {e}")
    except Exception as e: # pragma: no cover
        print(f"An unexpected error occurred in generate_consolidated_file: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    print("Python Project Consolidator for AI Context (v3 - Hardcoded Paths)")
    print("-----------------------------------------------------------------")
    print("This script walks through a predefined project directory, lists its structure,")
    print("finds all .py files, and consolidates them into a single text file.")
    print("The output includes the project structure at the top, followed by code blocks.\n")

    # --- Configuration: Hardcoded Paths ---
    # !!! IMPORTANT: If these paths are incorrect, please update them before running. !!!
    HARDCODED_PROJECT_ROOT_DIR = "c:/Users/Owner/Desktop/Self-Evolving-Agent-feat-learning-module/Self-Evolving-Agent-feat-learning-module/Self-Evolving-Agent-feat-chat-history-context/"
    HARDCODED_OUTPUT_FILENAME = "consolidated_project_for_ai.txt"
    # The output file will be placed in the directory where this script is run.
    # --- End Configuration ---

    project_to_scan_dir = HARDCODED_PROJECT_ROOT_DIR
    
    # Determine output path: in the Current Working Directory (where script is executed from)
    output_file_abs_path = os.path.abspath(HARDCODED_OUTPUT_FILENAME)
    
    # Get the basename of the script itself to avoid processing it.
    # os.path.basename(__file__) is generally robust for this.
    current_script_basename = os.path.basename(__file__) 

    print(f"Hardcoded project root to scan: {project_to_scan_dir}")
    print(f"Hardcoded output file name: {HARDCODED_OUTPUT_FILENAME} (will be saved to: {output_file_abs_path})")
    print(f"This script name (to ignore): {current_script_basename}\n")

    generate_consolidated_file(project_to_scan_dir, output_file_abs_path, current_script_basename)

    print(f"\n--- Instructions for use with AI ---")
    print(f"1. Open the generated file: '{output_file_abs_path}'")
    print(f"2. Review its contents (project structure at top, then code blocks).")
    print(f"3. Copy its entire content.")
    print(f"4. Paste it into your conversation with the AI for full project context.")
    print(f"5. Remind the AI about the structure section and the START/END FILE markers.")
    print(f"6. Remember this file is NOT MEANT TO BE EXECUTED.")
    print("---")