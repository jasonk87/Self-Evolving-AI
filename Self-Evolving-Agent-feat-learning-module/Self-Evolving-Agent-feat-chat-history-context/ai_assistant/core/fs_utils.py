# ai_assistant/core/fs_utils.py
import os
import logging

logger = logging.getLogger(__name__)

def write_to_file(filepath: str, content: str) -> bool:
    """
    Writes the given content string to the specified filepath.
    Ensures the directory for the filepath exists, creating it if necessary.

    Args:
        filepath: The absolute or relative path to the file.
        content: The string content to write to the file.

    Returns:
        True if writing was successful, False otherwise.
    """
    try:
        # Ensure the directory exists
        dir_path = os.path.dirname(filepath)
        if dir_path: # Check if there is a directory part
            os.makedirs(dir_path, exist_ok=True)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"Successfully wrote content to {filepath}")
        return True
    except IOError as e: # pragma: no cover
        logger.error(f"IOError writing to {filepath}: {e}", exc_info=True)
        return False
    except Exception as e: # pragma: no cover
        logger.error(f"Unexpected error writing to {filepath}: {e}", exc_info=True)
        return False

if __name__ == '__main__': # pragma: no cover
    # Configure basic logging for testing this module directly
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    test_dir = "test_fs_utils_output"
    test_filepath_success = os.path.join(test_dir, "subdir", "test_output.txt")
    test_content = "Hello, world!\nThis is a test file."

    print(f"Attempting to write to: {test_filepath_success}")
    success = write_to_file(test_filepath_success, test_content)

    if success:
        print(f"Successfully wrote to {test_filepath_success}. Verifying content...")
        try:
            with open(test_filepath_success, 'r', encoding='utf-8') as f_read:
                read_content = f_read.read()
            assert read_content == test_content
            print("Content verification successful.")
        except Exception as e_verify:
            print(f"Error verifying content: {e_verify}")
    else:
        print(f"Failed to write to {test_filepath_success}.")

    # Test failure case (e.g., by trying to write to a protected path, though hard to simulate reliably cross-platform)
    # For now, the success case demonstrates directory creation and writing.
    # A more robust test for failure would require mocking os.makedirs or open to raise IOError.

    # Cleanup (optional, comment out to inspect files)
    # import shutil
    # if os.path.exists(test_dir):
    #     print(f"Cleaning up test directory: {test_dir}")
    #     shutil.rmtree(test_dir)
    print(f"Test finished. Inspect '{test_dir}' if cleanup is commented out.")
