import os
import shutil
import datetime
from typing import List, Dict, Union, Optional
import logging

logger = logging.getLogger(__name__)

def modify_file_lines(
    file_path: str,
    edits: List[Dict[str, Union[int, str]]],
    backup: bool = True
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
        
    Example:
        modify_file_lines("example.py", [
            {"start": 5, "end": 5, "content": "    print('Modified line 5')"},
            {"start": 10, "end": 12, "content": "    # Replaced lines 10-12\n    return True"}
        ])
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
                 # Example: Edit 1: 10-12. Edit 2: 8-11. 
                 # Sorted: 10-12 (last_start becomes 10), then 8-11. End 11 >= 10.
                 return f"Error: Overlapping edits detected. Please merge overlapping ranges. Conflict near line {end}."
            
            last_start = start
            
            # Check bounds
            if start > total_lines + 1:
                # Appending way past end
                return f"Error: Start line {start} is beyond end of file ({total_lines})."
            
            # Adjust indices for 0-based list
            start_idx = start - 1
            end_idx = end # split is exclusive at end, so line 5 (idx 4) to 5 means [4:5]
            
            # If content implies multiple lines, ensure it ends with newline if replacing full lines
            # logic: line replacement usually expects strict line count handling.
            # But here "content" replaces the block.
            
            # Normalize content to list of lines
            new_lines_list = content.splitlines(keepends=True)
            if content and not content.endswith('\n'):
                 # If non-empty content doesn't end with newline, add it if it's not the very last line of file
                 # Actually properly handled by python list slicing usually
                 if new_lines_list:
                    new_lines_list[-1] = new_lines_list[-1] + '\n'
            
            # Apply splice
            if start_idx > len(lines):
                 # Append case if perfectly gapless? 
                 # Or fill with newlines?
                 # For simplicity, if start > len(lines), we append.
                 # But we checked start > total_lines + 1 above.
                 # If start == total_lines + 1, it's an append.
                 lines.extend(new_lines_list)
            else:
                lines[start_idx:end_idx] = new_lines_list
                
        # Write back
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
            
        return f"Success: Applied {len(validated_edits)} edits to {os.path.basename(file_path)}."
        
    except Exception as e:
        return f"Error modifying file: {str(e)}"

if __name__ == "__main__":
    # Test logic
    test_file = "test_surgical_edit.txt"
    with open(test_file, "w") as f:
        f.write("Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n")
        
    print(f"Original content:\n{open(test_file).read()}")
    
    # Test 1: Single line replacement
    result = modify_file_lines(test_file, [{"start": 2, "end": 2, "content": "Line 2 Modified\n"}], backup=False)
    print(f"Test 1 Result: {result}")
    print(f"Content:\n{open(test_file).read()}")
    
    # Test 2: Multi-line disjoint
    # Replace line 1 and lines 4-5
    result = modify_file_lines(test_file, [
        {"start": 1, "end": 1, "content": "Line 1 Updated\n"},
        {"start": 4, "end": 5, "content": "Lines 4-5 merged into one\n"}
    ], backup=False)
    print(f"Test 2 Result: {result}")
    print(f"Content:\n{open(test_file).read()}")
    
    # Cleanup
    if os.path.exists(test_file):
        os.remove(test_file)
