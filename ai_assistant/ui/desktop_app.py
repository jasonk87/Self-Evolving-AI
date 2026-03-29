import sys
import os
import threading
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QLineEdit, QTextBrowser, QGraphicsDropShadowEffect
)
from PyQt6.QtCore import Qt, QPoint, QEvent
from PyQt6.QtGui import QColor, QFont, QKeyEvent

try:
    import keyboard
except ImportError:
    keyboard = None
    print("Warning: 'keyboard' module not installed. Global hotkey disabled.")

class FloatingAgentUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.init_ui()
        self.setup_hotkey()

        # State for dragging the frameless window
        self.old_pos = None

    def init_ui(self):
        # Frameless and translucent window
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(700, 500)

        # Main central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Layout
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(20, 20, 20, 20)

        # Main container with styling
        self.container = QWidget()
        self.container.setObjectName("MainContainer")
        self.container.setStyleSheet("""
            QWidget#MainContainer {
                background-color: #1e1e24;
                border-radius: 12px;
                border: 1px solid #3f3f46;
            }
        """)

        # Add drop shadow
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(25)
        shadow.setColor(QColor(0, 0, 0, 150))
        shadow.setOffset(0, 10)
        self.container.setGraphicsEffect(shadow)

        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(15, 15, 15, 15)
        container_layout.setSpacing(10)

        # Chat History (Output)
        self.chat_display = QTextBrowser()
        self.chat_display.setStyleSheet("""
            QTextBrowser {
                background-color: transparent;
                color: #e4e4e7;
                border: none;
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 14px;
                padding: 5px;
            }
        """)
        self.chat_display.append("<b style='color: #a78bfa;'>System:</b> OS Agent initialized. Waiting for command...")

        # Input Bar
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Ask the agent to do something...")
        self.input_field.setStyleSheet("""
            QLineEdit {
                background-color: #27272a;
                color: #ffffff;
                border: 1px solid #52525b;
                border-radius: 8px;
                padding: 12px;
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 15px;
            }
            QLineEdit:focus {
                border: 1px solid #8b5cf6;
            }
        """)
        self.input_field.returnPressed.connect(self.handle_input)

        # Assemble
        container_layout.addWidget(self.chat_display)
        container_layout.addWidget(self.input_field)
        layout.addWidget(self.container)

        # Center on screen
        self.center_on_screen()

    def center_on_screen(self):
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = (screen.height() - self.height()) // 3  # Place slightly above center
        self.move(x, y)

    def handle_input(self):
        text = self.input_field.text().strip()
        if not text:
            return

        self.input_field.clear()

        # Display user input
        self.chat_display.append(f"<br><b style='color: #38bdf8;'>You:</b> {text}")

        # TODO: Wire this directly to ai_assistant.core.controller/orchestrator
        # For now, just echo.
        self.chat_display.append(f"<b style='color: #a78bfa;'>Agent:</b> Processing OS command '{text}'...")

        # Scroll to bottom
        scrollbar = self.chat_display.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

        # If user types exit, hide or close
        if text.lower() in ['exit', 'quit']:
            self.hide()

    def setup_hotkey(self):
        """Bind Ctrl+Space to toggle window visibility."""
        if keyboard:
            # We must use a separate thread to listen to the global hotkey safely,
            # or rely on an OS-specific message loop hook. Keyboard module handles this fine.
            try:
                keyboard.add_hotkey('ctrl+space', self.toggle_visibility_from_thread)
            except Exception as e:
                print(f"Could not bind hotkey: {e}")

    def toggle_visibility_from_thread(self):
        """Called by the keyboard thread."""
        # GUI operations must be done in the main thread
        QApplication.postEvent(self, QEvent(QEvent.Type.User))

    def customEvent(self, event):
        """Handle custom events, like toggling visibility from a background thread."""
        if event.type() == QEvent.Type.User:
            self.toggle_visibility()

    def toggle_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.activateWindow()
            self.input_field.setFocus()

    # --- Frameless Window Dragging Logic ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.old_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if self.old_pos is not None:
            delta = event.globalPosition().toPoint() - self.old_pos
            self.move(self.pos() + delta)
            self.old_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.old_pos = None

    def keyPressEvent(self, event: QKeyEvent):
        # Hide on escape
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        super().keyPressEvent(event)

def main():
    app = QApplication(sys.argv)

    # Optional: ensure we can safely exit via Ctrl+C in terminal
    import signal
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    window = FloatingAgentUI()
    window.show()

    print("Desktop OS Agent UI Started. Press 'Ctrl+Space' to toggle if hotkeys are enabled, or 'Escape' to hide.")
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
