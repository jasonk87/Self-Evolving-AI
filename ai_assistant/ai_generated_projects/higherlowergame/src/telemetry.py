import json

TELEMETRY_FILE = "telemetry.json"

def write_telemetry(game_state):
    """
    Writes the current game state to the telemetry file.

    Args:
        game_state (dict): A dictionary containing the current game state.
    """
    try:
        with open(TELEMETRY_FILE, "w") as f:
            json.dump(game_state, f, indent=4)
    except Exception as e:
        print(f"Error writing to telemetry file: {e}")

if __name__ == '__main__':
    # Example usage:
    initial_game_state = {
        "game_id": "12345",
        "player_name": "Test Player",
        "current_round": 1,
        "guesses": [],
        "high_score": 0
    }
    write_telemetry(initial_game_state)

    # Simulate updating the game state:
    updated_game_state = initial_game_state.copy()
    updated_game_state["guesses"].append(50)
    updated_game_state["current_round"] = 2
    write_telemetry(updated_game_state)