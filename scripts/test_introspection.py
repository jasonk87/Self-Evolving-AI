import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ai_assistant.custom_tools.introspection_tools import analyze_project_structure

print("Running System Audit...")
report = analyze_project_structure(output_format="summary")
print("\n--- REPORT START ---")
print(report[:1000]) # Print first 1000 chars to verify
print("...\n--- REPORT END ---")
print(f"Total Report Length: {len(report)} chars")
