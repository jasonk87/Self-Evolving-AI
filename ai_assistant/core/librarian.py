import os
import ast
import json
import logging
import asyncio
import hashlib
from typing import List, Dict, Any
from ai_assistant.core.memory_manager import MemoryManager
from ai_assistant.config import get_data_dir

logger = logging.getLogger(__name__)

class LibrarianAgent:
    """
    Acts as an internal cartographer for the AI. 
    Reads the project's source code, maps the architecture using AST, 
    and saves these technical facts into the AI's long-term memory (RAG).
    """

    def __init__(self, project_root: str, memory_manager: MemoryManager):
        self.project_root = project_root
        self.memory_manager = memory_manager
        self.state_file = os.path.join(get_data_dir(), "librarian_state.json")
        # State format: {filepath: {"mtime": float, "fact_hashes": [str]}}
        self.last_scan_state: Dict[str, Dict[str, Any]] = self._load_state()

    def _load_state(self) -> Dict[str, Dict[str, Any]]:
        """Loads the last modified times of scanned files."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Migration: if old format (str -> float), convert to new
                    if data and isinstance(next(iter(data.values())), float):
                        return {k: {"mtime": v, "fact_hashes": []} for k, v in data.items()}
                    return data
            except Exception as e:
                logger.error(f"LibrarianAgent: Failed to load state: {e}")
        return {}

    def _save_state(self):
        """Saves the current state of scanned files."""
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self.last_scan_state, f)
        except Exception as e:
            logger.error(f"LibrarianAgent: Failed to save state: {e}")

    async def scan_codebase(self):
        """
        Walks through the project root, parses .py files, and extracts facts.
        """
        logger.info(f"LibrarianAgent: Starting codebase scan at {self.project_root}...")
        files_processed = 0
        facts_added = 0

        for root, dirs, files in os.walk(self.project_root):
             # Skip hidden directories and virtual envs (basic heuristic)
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['venv', 'env', '__pycache__', 'node_modules']]

            for file in files:
                if file.endswith(".py") and "test" not in file.lower():
                    filepath = os.path.join(root, file)

                    # Diff check
                    try:
                        mtime = os.path.getmtime(filepath)
                        relative_path = os.path.relpath(filepath, self.project_root)

                        file_state = self.last_scan_state.get(filepath, {})
                        last_mtime = file_state.get("mtime", 0.0)
                        old_hashes = set(file_state.get("fact_hashes", []))

                        if last_mtime == mtime:
                            continue # Skip unchanged file

                        logger.debug(f"LibrarianAgent: Processing {relative_path}...")

                        # 1. Parse current facts
                        facts = self._process_file(filepath, relative_path)

                        # 2. Compute new hashes and map
                        new_fact_map = {}
                        for fact_text in facts:
                            fact_hash = hashlib.md5(fact_text.encode('utf-8')).hexdigest()
                            new_fact_map[fact_hash] = fact_text

                        new_hashes = set(new_fact_map.keys())

                        # 3. Calculate Delta
                        hashes_to_add = new_hashes - old_hashes
                        hashes_to_remove = old_hashes - new_hashes

                        stats_added = 0
                        stats_removed = 0

                        # 4. Remove deleted facts
                        for h in hashes_to_remove:
                            await self.memory_manager.delete_facts_by_source_metadata(
                                source="librarian",
                                additional_metadata={"filepath": relative_path, "fact_hash": h}
                            )
                            stats_removed += 1

                        # 5. Add new facts
                        for h in hashes_to_add:
                            fact_text = new_fact_map[h]
                            await self.memory_manager.add_fact_with_rag(
                                text=fact_text,
                                category="codebase_knowledge",
                                source="librarian",
                                filepath=relative_path,
                                fact_hash=h
                            )
                            facts_added += 1
                            stats_added += 1

                        # Update state
                        self.last_scan_state[filepath] = {
                            "mtime": mtime,
                            "fact_hashes": list(new_hashes)
                        }
                        files_processed += 1

                        if (stats_added > 0 or stats_removed > 0):
                             logger.info(f"LibrarianAgent: {relative_path} delta: +{stats_added} / -{stats_removed} facts")

                        # Yield control to event loop occasionally
                        if files_processed % 5 == 0:
                            await asyncio.sleep(0.01)

                    except Exception as e:
                        logger.error(f"LibrarianAgent: Error processing {filepath}: {e}", exc_info=True)

        self._save_state()
        if files_processed > 0:
            logger.info(f"LibrarianAgent: Scan complete. Processed {files_processed} files, added {facts_added} new facts.")
        else:
            logger.debug("LibrarianAgent: Scan complete. No new or modified files found.")

    def _process_file(self, filepath: str, relative_path: str) -> List[str]:
        """
        Parses a single file using AST and returns a list of natural language facts.
        """
        facts = []
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                source = f.read()

            tree = ast.parse(source)
            facts.extend(self._extract_metadata_from_ast(tree, relative_path))

        except SyntaxError as e:
            logger.warning(f"LibrarianAgent: SyntaxError in {relative_path}: {e}")
            facts.append(f"The file {relative_path} currently contains a syntax error: {e}")
        except Exception as e:
            logger.error(f"LibrarianAgent: Failed to parse {relative_path}: {e}")

        return facts

    def _extract_metadata_from_ast(self, tree: ast.AST, filename: str) -> List[str]:
        """
        Extracts classes, functions, imports, and inheritance from AST.
        """
        facts = []

        # Module level definition
        module_name = filename.replace(os.sep, ".").replace(".py", "")
        facts.append(f"The module {module_name} is located at {filename}.")

        for node in ast.walk(tree):
            # Classes
            if isinstance(node, ast.ClassDef):
                class_name = node.name
                bases = []
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        bases.append(base.id)
                    elif isinstance(base, ast.Attribute):
                        bases.append(f"{base.value.id if isinstance(base.value, ast.Name) else '?'}.{base.attr}")

                fact = f"The file {filename} defines the class {class_name}."
                if bases:
                    fact += f" It inherits from: {', '.join(bases)}."
                facts.append(fact)

                # Methods within class
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and not item.name.startswith("_"):
                         facts.append(f"The class {class_name} in {filename} has a method named {item.name}.")

            # Top-level Functions
            elif isinstance(node, ast.FunctionDef):
                # Check if it's top level by ensuring it's not inside a class (basic check: parent is Module)
                # ast.walk doesn't give parent info easily without custom traversal.
                # However, for RAG, simple existence is good enough.
                # We can try to distinguish, but basic "file defines function" is okay.
                # A better way is to iterate explicitly over tree.body.
                pass

            # Imports
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    facts.append(f"The module {module_name} imports the module {alias.name}.")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    facts.append(f"The module {module_name} imports from {node.module}.")

        # Iterate body for top-level functions to avoid confusion with methods
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                 facts.append(f"The file {filename} defines a global function named {node.name}.")

        return facts
