import re
with open("ai_assistant/ui/desktop_app.py", "r") as f:
    content = f.read()

# Add necessary imports
imports = """
import asyncio
from PyQt6.QtCore import pyqtSignal, QObject
from ai_assistant.core.controller import SystemController

class WorkerSignals(QObject):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

"""

if "import asyncio" not in content:
    content = content.replace("from PyQt6.QtCore import Qt, QPoint, QEvent", "from PyQt6.QtCore import Qt, QPoint, QEvent\n" + imports)

# Initialize Controller in init
init_search = """        # State for dragging the frameless window
        self.old_pos = None"""

init_replace = """        # State for dragging the frameless window
        self.old_pos = None

        # Initialize AI Brain
        self.controller = SystemController()
        self.signals = WorkerSignals()
        self.signals.finished.connect(self.display_agent_response)
        self.signals.error.connect(self.display_agent_error)"""

content = content.replace(init_search, init_replace)

# Modify handle_input to run async orchestrator in a thread
input_search = """        # TODO: Wire this directly to ai_assistant.core.controller/orchestrator
        # For now, just echo.
        self.chat_display.append(f"<b style='color: #a78bfa;'>Agent:</b> Processing OS command '{text}'...")"""

input_replace = """        self.chat_display.append(f"<b style='color: #a78bfa;'>Agent:</b> Thinking...")
        self.input_field.setDisabled(True)

        # Run AI task in background thread to keep GUI responsive
        threading.Thread(target=self.run_orchestrator, args=(text,), daemon=True).start()

    def run_orchestrator(self, prompt: str):
        try:
            # We must create a new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            state = loop.run_until_complete(self.controller.process_request(prompt))
            loop.close()

            if state.final_answer:
                self.signals.finished.emit(state.final_answer)
            else:
                self.signals.finished.emit("Done.")
        except Exception as e:
            self.signals.error.emit(str(e))

    def display_agent_response(self, text: str):
        # Remove the 'Thinking...' line
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.select(cursor.SelectionType.BlockUnderCursor)
        cursor.removeSelectedText()
        cursor.deletePreviousChar() # Remove newline

        import markdown
        html = markdown.markdown(text)
        self.chat_display.append(f"<br><b style='color: #a78bfa;'>Agent:</b> {html}")
        self.input_field.setDisabled(False)
        self.input_field.setFocus()

        scrollbar = self.chat_display.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def display_agent_error(self, text: str):
        self.input_field.setDisabled(False)
        self.chat_display.append(f"<br><b style='color: #ef4444;'>System Error:</b> {text}")
        scrollbar = self.chat_display.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())"""

content = content.replace(input_search, input_replace)

with open("ai_assistant/ui/desktop_app.py", "w") as f:
    f.write(content)
print("Wired Orchestrator into PyQt6 App")
