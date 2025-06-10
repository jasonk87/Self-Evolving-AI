def find_project_directory(project_name: str) -> str:
    """
    Finds and returns the absolute path of the project directory with the given name.
    
    Args:
        project_name (str): The name of the project directory to find.
        
    Returns:
        str: The absolute path to the project directory.
        
    Raises:
        FileNotFoundError: If the project directory is not found.
    """
    import os
    project_path = os.path.join(os.getcwd(), project_name)
    if os.path.isdir(project_path):
        return project_path
    else:
        raise FileNotFoundError(f"Project directory '{project_name}' not found.")