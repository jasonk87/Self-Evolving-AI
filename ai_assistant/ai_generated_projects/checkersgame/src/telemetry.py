import json
import logging

# Configure logging
logging.basicConfig(filename='telemetry.log', level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

def write_telemetry(game_state, event_type, event_data=None):
    """
    Writes game state and events to the telemetry log and telemetry.json.

    Args:
        game_state (dict): The current state of the game.
        event_type (str): The type of event that occurred (e.g., "move", "capture").
        event_data (dict, optional): Additional data associated with the event. Defaults to None.
    """
    try:
        # Log the event
        log_message = f"Event: {event_type}, Game State: {game_state}"
        if event_data:
            log_message += f", Data: {event_data}"
        logging.info(log_message)

        # Write game state to telemetry.json
        with open('telemetry.json', 'w') as f:
            json.dump(game_state, f, indent=4)  # Pretty print JSON

    except Exception as e:
        logging.error(f"Error writing telemetry: {e}")

if __name__ == '__main__':
    # Example usage:
    initial_game_state = {
        "board": [
            [" ", "b", " ", "b", " ", "b", " ", "b"],
            ["b", " ", "b", " ", "b", " ", "b", " "],
            [" ", "b", " ", "b", " ", "b", " ", "b"],
            [" ", " ", " ", " ", " ", " ", " ", " "],
            [" ", " ", " ", " ", " ", " ", " ", " "],
            ["w", " ", "w", " ", "w", " ", "w", " "],
            [" ", "w", " ", "w", " ", "w", " ", "w"],
            ["w", " ", "w", " ", "w", " ", "w", " "]
        ],
        "current_player": "w",
        "move_count": 0
    }

    write_telemetry(initial_game_state, "game_start")

    # Simulate a move
    new_game_state = initial_game_state.copy()
    new_game_state["board"][5][0] = " "
    new_game_state["board"][4][1] = "w"
    new_game_state["current_player"] = "b"
    new_game_state["move_count"] += 1
    
    move_data = {"from": (5, 0), "to": (4, 1)}
    write_telemetry(new_game_state, "move", move_data)