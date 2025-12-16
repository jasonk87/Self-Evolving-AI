import json
import datetime

TELEMETRY_FILE = "telemetry.json"

def write_telemetry(game_state, event_type, event_data=None):
    """
    Writes game state and progress information to the telemetry file.

    Args:
        game_state (dict): A dictionary representing the current state of the game.
        event_type (str): A string describing the type of event being recorded (e.g., "move", "capture", "game_start", "game_end").
        event_data (dict, optional): A dictionary containing additional data associated with the event. Defaults to None.
    """

    timestamp = datetime.datetime.now().isoformat()

    telemetry_entry = {
        "timestamp": timestamp,
        "event_type": event_type,
        "game_state": game_state,
        "event_data": event_data
    }

    try:
        with open(TELEMETRY_FILE, "a") as f:  # Open in append mode
            f.write(json.dumps(telemetry_entry) + "\n") # Write each entry as a separate line

    except Exception as e:
        print(f"Error writing to telemetry file: {e}")

if __name__ == '__main__':
    # Example Usage
    initial_game_state = {
        "board": [
            [None, "b", None, "b", None, "b", None, "b"],
            ["b", None, "b", None, "b", None, "b", None],
            [None, "b", None, "b", None, "b", None, "b"],
            [None, None, None, None, None, None, None, None],
            [None, None, None, None, None, None, None, None],
            ["r", None, "r", None, "r", None, "r", None],
            [None, "r", None, "r", None, "r", None, "r"],
            ["r", None, "r", None, "r", None, "r", None]
        ],
        "current_player": "r",
        "red_pieces": 12,
        "black_pieces": 12
    }

    write_telemetry(initial_game_state, "game_start")

    updated_game_state = {
        "board": [
            [None, "b", None, "b", None, "b", None, "b"],
            ["b", None, "b", None, "b", None, "b", None],
            [None, "b", None, "b", None, "b", None, None],
            [None, None, None, None, None, None, None, "b"],
            [None, None, None, None, None, None, None, None],
            ["r", None, "r", None, "r", None, "r", None],
            [None, "r", None, "r", None, "r", None, "r"],
            ["r", None, "r", None, "r", None, "r", None]
        ],
        "current_player": "b",
        "red_pieces": 12,
        "black_pieces": 12
    }

    move_data = {"from": (5, 0), "to": (4, 1)}
    write_telemetry(updated_game_state, "move", move_data)

    capture_game_state = {
        "board": [
            [None, "b", None, "b", None, "b", None, "b"],
            ["b", None, "b", None, "b", None, "b", None],
            [None, "b", None, "b", None, "b", None, None],
            [None, None, None, None, None, None, None, "b"],
            [None, None, None, None, None, None, None, None],
            [None, None, "r", None, "r", None, "r", None],
            [None, "r", None, "r", None, "r", None, "r"],
            ["r", None, "r", None, "r", None, "r", None]
        ],
        "current_player": "b",
        "red_pieces": 11,
        "black_pieces": 12
    }
    capture_data = {"captured_piece_location": (4,1)}
    write_telemetry(capture_game_state, "capture", capture_data)

    final_game_state = {
        "winner": "b"
    }
    write_telemetry(final_game_state, "game_end")