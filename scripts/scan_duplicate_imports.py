import os
import re

GENERATED_TOOLS_DIR = r"c:\Users\Jason\Desktop\Self Evolving AI\ai_assistant\custom_tools\generated"

def scan_for_duplicates():
    print(f"Scanning {GENERATED_TOOLS_DIR} for duplicate imports...")
    affected_files = []
    
    if not os.path.exists(GENERATED_TOOLS_DIR):
        print("Directory not found.")
        return

    for filename in os.listdir(GENERATED_TOOLS_DIR):
        if not filename.endswith(".py"):
            continue
            
        filepath = os.path.join(GENERATED_TOOLS_DIR, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        seen_imports = set()
        duplicates_found = 0
        
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                if stripped in seen_imports:
                    duplicates_found += 1
                else:
                    seen_imports.add(stripped)
        
        if duplicates_found > 0:
            print(f"FAILED: {filename} has {duplicates_found} duplicate import lines.")
            affected_files.append(filepath)
        else:
            # print(f"OK: {filename}")
            pass
            
    if not affected_files:
        print("No duplicate imports found.")
    else:
        print(f"\nFound {len(affected_files)} affected files.")

if __name__ == "__main__":
    scan_for_duplicates()
