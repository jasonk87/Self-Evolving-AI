import re

with open("ai_assistant/custom_tools/background_automation_tools.py", "r") as f:
    content = f.read()

# Fix command injection in background_automation_tools
if "import shlex" not in content:
    content = content.replace("import subprocess", "import subprocess\nimport shlex")

search = """            # Safely replace the {filepath} variable and execute the bash command
            final_command = self.command_template.replace("{filepath}", f'"{filepath}"')"""

replace = """            # Safely replace the {filepath} variable using shlex.quote to prevent command injection
            safe_filepath = shlex.quote(filepath)
            final_command = self.command_template.replace("{filepath}", safe_filepath)"""

content = content.replace(search, replace)

with open("ai_assistant/custom_tools/background_automation_tools.py", "w") as f:
    f.write(content)
print("Fixed background tools injection")
