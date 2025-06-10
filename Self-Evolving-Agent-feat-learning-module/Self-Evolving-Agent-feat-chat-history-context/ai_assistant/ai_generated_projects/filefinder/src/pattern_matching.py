import re

def match_pattern(filename, pattern):
    """
    Matches a filename against a given search pattern.

    Args:
        filename (str): The filename to search against.
        pattern (str): The search pattern.

    Returns:
        list: A list of filenames that match the pattern.
              Returns an empty list if no files match.
    """
    try:
        regex = re.compile(pattern)
        matches = []
        if isinstance(filename, list):
            for file in filename:
                if regex.search(file):
                    matches.append(file)
        else:
            if regex.search(filename):
                matches.append(filename)
            
        return matches
    except re.error:
        print("Invalid regular expression pattern.")
        return []