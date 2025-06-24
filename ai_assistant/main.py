import sys
import os
import shutil # For deleting directory contents

# Add the project root directory (which is one level up from the 'ai_assistant' directory)
# to Python's module search path. This allows Python to find the 'ai_assistant' package.
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Now, your original imports should work correctly
from ai_assistant.communication.cli import start_cli
# Import the consolidated background service management functions
from ai_assistant.core.background_service import start_background_services, stop_background_services
# Import config settings
from ai_assistant.config import CLEAR_EXISTING_KNOWLEDGE_ON_STARTUP, get_data_dir
# Imports for core services (ReflectionLog).
# Their persisted data (e.g., JSON files in the data_dir) is targeted by
# 'clear_knowledge_if_configured', which directly deletes files.
# These classes are likely used by other components initialized via main.py.
from ai_assistant.core.reflection import ReflectionLog # Manages reflection_log.json
import asyncio
import logging

# Setup basic logging configuration for the application
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def clear_knowledge_if_configured():
    """
    Clears stored knowledge (files and subdirectories in the data directory)
    if the CLEAR_EXISTING_KNOWLEDGE_ON_STARTUP flag is set to True.
    """
    if CLEAR_EXISTING_KNOWLEDGE_ON_STARTUP:
        data_dir = get_data_dir() # This function also creates the dir if it doesn't exist
        logger.info(f"CLEAR_EXISTING_KNOWLEDGE_ON_STARTUP is True. Attempting to clear knowledge in: {data_dir}")

        # Ask for user confirmation before proceeding
        confirm = input(f"WARNING: CLEAR_EXISTING_KNOWLEDGE_ON_STARTUP is set to True. "
                        f"This will delete all data in '{data_dir}'.\n"
                        f"Are you sure you want to proceed? (yes/no): ").strip().lower()

        if confirm == 'yes':
            logger.info(f"User confirmed. Proceeding with clearing knowledge in: {data_dir}")
            if os.path.exists(data_dir) and os.path.isdir(data_dir):
                for item_name in os.listdir(data_dir):
                    item_path = os.path.join(data_dir, item_name)
                    try:
                        if os.path.isfile(item_path) or os.path.islink(item_path):
                            os.unlink(item_path)
                            logger.info(f"Deleted file: {item_path}")
                        elif os.path.isdir(item_path):
                            shutil.rmtree(item_path) # Recursively delete directory and its contents
                            logger.info(f"Deleted directory and its contents: {item_path}")
                    except Exception as e:
                        logger.error(f"Failed to delete {item_path}. Reason: {e}")
                logger.info(f"Knowledge clearing process complete for {data_dir}.")
            else:
                logger.warning(f"Data directory {data_dir} was not found or is not a directory. No knowledge to clear.")
        else:
            logger.info("User aborted knowledge clearing process. No data will be deleted.")
            # Optionally, you might want to exit or set CLEAR_EXISTING_KNOWLEDGE_ON_STARTUP to False programmatically
            # For now, it just logs and continues, respecting the original flag for this session if not cleared.
    else:
        logger.info("CLEAR_EXISTING_KNOWLEDGE_ON_STARTUP is False. Skipping knowledge clearing.")

async def async_main_runner():
    """
    Asynchronous main function to orchestrate application startup and shutdown.
    """
    try:
        # Perform knowledge clearing first, if configured
        clear_knowledge_if_configured()

        logger.info("Main: Starting application and background services...")
        # start_background_services() is synchronous but relies on a running event loop
        # to create its async task. asyncio.run() provides this loop.
        start_background_services()

        logger.info("Main: Starting CLI...")
        await start_cli() # start_cli must be an async function
    finally:
        logger.info("Main (async_main_runner finally): Cleaning up background services...")
        await stop_background_services() # stop_background_services is async
        logger.info("Main (async_main_runner finally): Background services cleanup attempt complete.")

if __name__ == "__main__":
    try:
        asyncio.run(async_main_runner())
    except KeyboardInterrupt:
        logger.info("\nMain: Keyboard interrupt received by top-level handler. Application will exit.")
    except Exception as e:
        logger.error(f"Main: An unexpected error occurred at the top level: {e}", exc_info=True)
    finally:
        logger.info("Main: Application shutdown sequence finished.")