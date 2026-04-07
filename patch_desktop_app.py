import re

with open("ai_assistant/ui/desktop_app.py", "r") as f:
    content = f.read()

# Add a session_id generation
if "import uuid" not in content:
    content = content.replace("import threading", "import threading\nimport uuid")

search = """        # Initialize AI Brain
        self.controller = SystemController()"""

replace = """        # Initialize AI Brain
        # Controller requires an orchestrator instance per its init
        from ai_assistant.core.orchestrator import DynamicOrchestrator
        self.controller = SystemController(orchestrator=DynamicOrchestrator())
        self.session_id = uuid.uuid4().hex"""

content = content.replace(search, replace)

search_call = """state = loop.run_until_complete(self.controller.handle_user_request(prompt))"""
replace_call = """state = loop.run_until_complete(self.controller.handle_user_request(prompt=prompt, session_id=self.session_id))"""

content = content.replace(search_call, replace_call)

with open("ai_assistant/ui/desktop_app.py", "w") as f:
    f.write(content)
print("Fixed desktop app method call")
