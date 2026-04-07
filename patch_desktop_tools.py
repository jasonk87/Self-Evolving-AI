import re

with open("ai_assistant/custom_tools/desktop_automation_tools.py", "r") as f:
    content = f.read()

# Fix command injection in desktop_automation_tools
if "import shlex" not in content:
    content = content.replace("import subprocess", "import subprocess\nimport shlex")

search = """        elif system == "Windows":
            # On Windows, 'start' is a shell builtin, so we use shell=True.
            # If a file is provided, we can try to launch the app against it.
            if file_path:
                command = f"start {app_name} \\\"{file_path}\\""
            else:
                command = f"start {app_name}" """

replace = """        elif system == "Windows":
            # Safely quote arguments to prevent command injection
            safe_app = shlex.quote(app_name)
            if file_path:
                safe_file = shlex.quote(file_path)
                command = f"start {safe_app} {safe_file}"
            else:
                command = f"start {safe_app}" """

content = content.replace(search, replace)

with open("ai_assistant/custom_tools/desktop_automation_tools.py", "w") as f:
    f.write(content)
print("Fixed desktop tools injection")
