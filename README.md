# Self-Evolving Autonomous AI Assistant

This project is a next-generation AI agent designed not just to execute tasks, but to **evolve, learn, and improve itself** over time. It features a sophisticated "Deep Space" themed Web UI ("The AI Cockpit") and a robust backend architecture for autonomous software development.

## 🚀 Key Capabilities

### 1. **Proactive Immunity (Test-Driven Evolution)**
   - **What it is:** When the AI writes a new tool for itself, it doesn't just hope it works. It *immediately* writes a comprehensive `pytest` test suite for it.
   - **Benefit:** Ensures that new skills are verified instantly and prevents regression.
   - **Components:** `generate_new_tool_from_description` (auto-test gen), `run_regression_tests`.

### 2. **RAG Memory System ("Wisdom")**
   - **What it is:** A Retrieval-Augmented Generation system that gives the AI long-term memory. It indexes facts and past experiences using vector embeddings (via Ollama/Numpy).
   - **Benefit:** Before planning a task, the AI asks *"How did I solve this before?"* or *"What mistakes did I make?"*, allowing it to learn from history.
   - **Components:** `VectorStore` (JSON-based), `RAGSystem`, `MemoryManager`.

### 3. **"The Council" (Adversarial Review)**
   - **What it is:** For high-risk tasks (like modifying its own source code), the AI convenes a digital "Council."
     - **The Skeptic:** Aggressively attacks the proposed code, looking for security flaws or bugs.
     - **The Judge:** Weighs the proposal against the critique and issues a final verdict.
   - **Benefit:** Drastically reduces the risk of "hallucinated" code breaking the system.
   - **Components:** `CriticalReviewCoordinator`, `ActionExecutor` integration.

### 4. **Visual "AI Cockpit"**
   - **What it is:** A sleek, "Deep Space" themed web interface that visualizes the AI's internal state.
   - **Features:**
     - Real-time Telemetry (memory usage, task status).
     - "The Council" visualization (watch the Skeptic and Judge debate).
     - File Explorer and Terminal output.
     - Interactive Chat.

### 5. **Unified Code Writing System (UCWS)**
   - **What it is:** A centralized engine for all code generation tasks, ensuring consistent quality and style.
   - **Capabilities:**
     - **Tool Creation:** Generates Python tools from natural language descriptions.
     - **Self-Healing:** Automatically fixes bugs in existing tools based on error logs (`ActionExecutor` + `CodeService`).
     - **Project Scaffolding:** Can initiate and build out multi-file software projects.

### 6. **Hierarchical Planning**
   - **What it is:** For complex goals (e.g., "Build a Snake game"), the AI breaks the task down into a high-level project plan and then executes it step-by-step.
   - **Benefit:** Allows the AI to handle large, multi-stage projects without getting lost.

### 7. **Evolutionary Architect**
   - **What it is:** A background autonomous process that periodically "audits" the codebase while the system is idle.
   - **Capabilities:**
     - **Static Analysis Filter:** Uses Python's `ast` to filter out clean code and identify files with high complexity, deprecated patterns, or TODOs.
     - **Evolutionary Lenses:** Generates optimization, modernization, or completion proposals using specialized LLM prompts.
     - **Safety:** Only generates *proposals* (Notifications). Never modifies code without user confirmation.

### 8. **Visual Memory Explorer ("Cortex")**
   - **What it is:** An interactive visualization of the AI's long-term memory.
   - **Benefit:** Allows the user to "see what the AI knows."
   - **Features:**
     - **Graph View:** Displays Facts and Insights as connected nodes in a force-directed graph.
     - **Management:** Users can inspect memory details and delete incorrect facts to correct the AI's behavior.

## 🛠️ Architecture

*   **Orchestrator:** `DynamicOrchestrator` manages the lifecycle of user requests, coordinating the Planner, Executor, and Learning agents.
*   **Planner:** `PlannerAgent` creates execution plans, now augmented with RAG-based memory lookup.
*   **Executor:** `ActionExecutor` carries out the plan, managing tools and code execution.
*   **Memory:** `MemoryManager` handles persistent facts and vector-based context retrieval.
*   **Interface:** Flask + SocketIO based web app (`web_app.py`).

## 📦 Requirements

*   Python 3.12+
*   `requests`, `aiohttp`, `numpy`, `pytest`, `flask`, `flask-socketio`, `eventlet`, `google-generativeai` (or Ollama).
*   **Ollama** (for local embeddings and optional LLM support).

## 🏃‍♂️ Getting Started

1.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
2.  **Start the AI Cockpit:**
    ```bash
    python web_app.py
    ```
3.  **Open your browser:** Navigate to `http://localhost:5000`.

## 🤖 Evolution Status

*   **Architecture:** Evolutionary (Background Auditing Active)
*   **Self-Modification:** ENABLED (Protected by The Council)
*   **Memory:** RAG-Enhanced & Visually Explorable
*   **Testing:** Test-Driven
*   **Planning:** Hierarchical & Wisdom-Augmented
