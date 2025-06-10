def save_to_file(items, filename="todo.txt"):
    """Saves a list of to-do items to a file."""
    try:
        with open(filename, "w") as f:
            for item in items:
                f.write(item + "\n")
    except Exception as e:
        print(f"Error saving to file: {e}")


def load_from_file(filename="todo.txt"):
    """Loads to-do items from a file."""
    try:
        with open(filename, "r") as f:
            items = [line.strip() for line in f]
        return items
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"Error loading from file: {e}")
        return []