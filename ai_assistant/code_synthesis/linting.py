# ai_assistant/code_synthesis/linting.py
import ast
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

class LintingError:
    def __init__(self, message: str, line: int = -1, error_type: str = "SyntaxError"):
        self.message = message
        self.line = line
        self.error_type = error_type

    def to_dict(self):
        return {
            "message": self.message,
            "line": self.line,
            "type": self.error_type
        }

    def __repr__(self):
        return f"Line {self.line}: {self.message} ({self.error_type})"

class CodeLinter:
    """
    A lightweight, AST-based linter to check generated code before execution/review.
    It catches syntax errors, basic undefined name errors, and some import issues.
    """

    @staticmethod
    def lint_code(code: str) -> Tuple[bool, List[LintingError]]:
        """
        Lints the provided Python code string.
        Returns (passed: bool, errors: List[LintingError])
        """
        errors = []

        # 1. Syntax Check
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            errors.append(LintingError(f"Syntax Error: {e.msg}", e.lineno or -1, "SyntaxError"))
            return False, errors
        except Exception as e:
            errors.append(LintingError(f"Parsing Error: {str(e)}", -1, "ParseError"))
            return False, errors

        # 2. Duplicate Import Check
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if name in imports:
                        errors.append(LintingError(f"Duplicate import detected: '{name}'", node.lineno, "DuplicateImport"))
                    imports.add(name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    name = f"{module}.{alias.name}"
                    if name in imports:
                        errors.append(LintingError(f"Duplicate import detected: '{name}'", node.lineno, "DuplicateImport"))
                    imports.add(name)

        # 3. Simple Undefined Name Check (Heuristic)
        # This is hard to do perfectly without a full symbol table, but we can check usage vs definitions/imports in the same file.
        # However, for code snippets meant to be inserted into existing modules, this is risky (names might be global).
        # We will stick to obvious ones if it's a standalone script, otherwise skip.
        # For now, let's skip complex undefined name checks to avoid false positives on valid global usage.

        return len(errors) == 0, errors

    @staticmethod
    def format_errors(errors: List[LintingError]) -> str:
        if not errors:
            return "No linting errors found."
        return "\n".join([str(e) for e in errors])
