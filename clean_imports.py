import os

GENERATED_TOOLS_DIR = r"c:\Users\Jason\Desktop\Self Evolving AI\ai_assistant\custom_tools\generated"
AFFECTED_FILES = [
    "generate_safe_html.py",
    "div_table_generator.py",
    "weather_tool.py",
    "location_utils.py"
]

def clean_imports():
    print("Cleaning imports...")
    for filename in AFFECTED_FILES:
        filepath = os.path.join(GENERATED_TOOLS_DIR, filename)
        if not os.path.exists(filepath):
            print(f"Skipping {filename} (not found)")
            continue
            
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        seen_imports = set()
        new_lines = []
        dup_count = 0
        
        for line in lines:
            stripped = line.strip()
            # Be mindful of multi-line from-imports, but typically these tools generate single line imports
            if stripped.startswith("import ") or stripped.startswith("from "):
                if stripped in seen_imports:
                    dup_count += 1
                    continue
                seen_imports.add(stripped)
            new_lines.append(line)
            
        if dup_count > 0:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
            print(f"Fixed {filename}: Removed {dup_count} duplicate lines.")
        else:
            print(f"No duplicates found in {filename}.")

if __name__ == "__main__":
    clean_imports()
