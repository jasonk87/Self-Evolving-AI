import json

TELEMETRY_FILENAME = "telemetry.json"

def save_game_state(game_state):
    """
    Saves the current game state to the telemetry.json file.

    Args:
        game_state (dict): A dictionary representing the current state of the game.
                           Expected keys might include 'board', 'current_player', 'winner', 'game_over'.
    """
    try:
        with open(TELEMETRY_FILENAME, 'w') as f:
            json.dump(game_state, f, indent=4)
    except IOError as e:
        print(f"Error: Could not save game state to {TELEMETRY_FILENAME}. {e}")
    except TypeError as e:
        print(f"Error: Game state is not JSON serializable. {e}")

def load_game_state():
    """
    Loads the game state from the telemetry.json file.
    If the file does not exist or is invalid, it returns a default initial game state.

    Returns:
        dict: The loaded game state, or a default initial state if loading fails.
    """
    default_state = {
        'board': [' '] * 9,  # Represents a 3x3 board with empty spaces
        'current_player': 'X',
        'winner': None,
        'game_over': False
    }
    try:
        with open(TELEMETRY_FILENAME, 'r') as f:
            game_state = json.load(f)
            # Optional: Add basic validation here if needed, e.g., check for essential keys
            # For now, assume loaded data is valid if it can be parsed.
            return game_state
    except FileNotFoundError:
        # If the file doesn't exist, it means it's the first run or telemetry was deleted.
        # Return the default initial state.
        return default_state
    except json.JSONDecodeError:
        # If the file exists but is empty or corrupted, json.load will raise this error.
        # Return the default initial state and inform the user.
        print(f"Warning: {TELEMETRY_FILENAME} is empty or corrupted. Starting a new game.")
        return default_state
    except IOError as e:
        # Catch other potential IO errors during reading.
        print(f"Error: Could not load game state from {TELEMETRY_FILENAME}. {e}")
        return default_state
