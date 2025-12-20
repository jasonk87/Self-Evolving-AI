import os
import sys
import shutil
import logging
import time
import datetime

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("SafeBoot")

def get_project_root():
    # Use this file location to determine root
    # This file is in scripts/
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def check_chroma_integrity(data_dir):
    chroma_dir = os.path.join(data_dir, "chroma_db")
    if not os.path.exists(chroma_dir):
        logger.info("ChromaDB directory does not exist. Safe to start (will be created).")
        return True

    logger.info(f"Checking integrity of ChromaDB at {chroma_dir}...")
    
    try:
        import chromadb
        # Attempt minimal client init and list collections
        client = chromadb.PersistentClient(path=chroma_dir)
        collections = client.list_collections()
        logger.info(f"Integrity Check Passed. Found collections: {[c.name for c in collections]}")
        return True
    except Exception as e:
        logger.error(f"ChromaDB Integrity Check FAILED: {e}")
        return False

def safe_boot():
    project_root = get_project_root()
    
    # Try to resolve data_dir via ai_assistant.config if possible, else default
    # We need to import ai_assistant.config, so add src to path
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    
    try:
        from ai_assistant.config import get_data_dir
        data_dir = get_data_dir()
    except Exception as e:
        logger.warning(f"Could not import config to find data_dir: {e}. Using default '_memory_'")
        data_dir = os.path.join(project_root, "_memory_")

    is_healthy = check_chroma_integrity(data_dir)

    if not is_healthy:
        chroma_dir = os.path.join(data_dir, "chroma_db")
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"chroma_db_corrupt_backup_{timestamp}"
        backup_path = os.path.join(data_dir, backup_name)
        
        logger.warning(f"Moving corrupt DB to {backup_path}...")
        try:
            # On Windows, rename might fail if process lock. ensuring no python process holds it is key.
            # This script runs before main app, so it should be fine unless residual zombie process.
            if os.path.exists(chroma_dir):
                shutil.move(chroma_dir, backup_path)
                logger.info("Corrupt DB moved successfully. System will start with fresh DB.")
            else:
                logger.warning("Chroma dir disappeared during check?")
        except Exception as e:
            logger.critical(f"Failed to move corrupt DB: {e}. Manual intervention required.")
            sys.exit(1)
    
    logger.info("Safe Boot Check Complete. Exiting with success.")
    sys.exit(0)

if __name__ == "__main__":
    safe_boot()
