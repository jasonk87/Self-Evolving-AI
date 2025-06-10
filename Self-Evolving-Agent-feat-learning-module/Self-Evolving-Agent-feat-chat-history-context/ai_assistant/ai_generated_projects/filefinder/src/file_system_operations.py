import os

def list_files_in_directory(directory_path):
    """
    Lists all files in a directory.

    Args:
        directory_path (str): The path to the directory.

    Returns:
        list: A list of filenames within the directory.
              Returns an empty list if the directory is empty or if an error occurs.
    """
    try:
        filenames = os.listdir(directory_path)
        return filenames
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"An error occurred: {e}")
        return []

def check_file_exists(file_path):
    """
    Checks if a file exists.

    Args:
        file_path (str): The path to the file.

    Returns:
        bool: True if the file exists, False otherwise.
    """
    return os.path.exists(file_path)