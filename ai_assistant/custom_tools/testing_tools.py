import subprocess
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

def run_regression_tests(test_path: Optional[str] = None) -> str:
    """
    Runs the regression suite (or specific tests) using pytest.
    This ensures that the AI's capabilities (tools) are still working as expected.

    Args:
        test_path: Optional path to run tests on. Defaults to 'tests/custom_tools/'.

    Returns:
        A string containing the output of the test run (stdout and stderr).
    """
    if not test_path:
        test_path = "tests/custom_tools/"

    # Ensure the path exists
    if not os.path.exists(test_path):
        return f"Error: Test path '{test_path}' does not exist."

    logger.info(f"Running regression tests on: {test_path}")

    try:
        # Run pytest as a subprocess
        result = subprocess.run(
            ["pytest", test_path, "-v"],
            capture_output=True,
            text=True,
            timeout=300 # 5 minute timeout
        )

        output = f"Exit Code: {result.returncode}\n\nSTDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"

        if result.returncode == 0:
            return f"Regression Tests PASSED.\n{output}"
        else:
            return f"Regression Tests FAILED.\n{output}"

    except subprocess.TimeoutExpired:
        return "Regression Tests TIMED OUT after 300 seconds."
    except Exception as e:
        logger.error(f"Error running regression tests: {e}", exc_info=True)
        return f"An error occurred while running regression tests: {e}"
