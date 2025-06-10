import logging

def handle_file_not_found_error(filename):
    """
    Handles the FileNotFoundError exception.
    """
    try:
        raise FileNotFoundError(f"File not found: {filename}")
    except Exception as e:
        logging.error(f"Error handling file not found: {e}")
        raise

def log_search_results(results):
    """
    Logs the search results.
    """
    log_message = f"Search Results: {results}"
    logging.info(log_message)