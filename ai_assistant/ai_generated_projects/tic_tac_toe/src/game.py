import json

# Assume telemetry.py exists and has a log_state function
# For this implementation, we'll create a dummy telemetry module if it's not found
try:
    import telemetry
except ImportError:
    class DummyTelemetry:
        def log_state(self, state_dict):
            telemetry_file = "telemetry.json"
            # Ensure directory exists if needed, though telemetry.json is usually in root
            # os.makedirs(os.path.dirname(telemetry_file), exist_ok=True)
            with open(telemetry_file, 'w') as f:
                json.dump(state_dict, f, indent=4)
            # print(f"Telemetry logged: {state_dict}") # Optional: for debugging

    telemetry = DummyTelemetry()

BOARD_SIZE = 3
EMPTY_CELL = ' '
PLAYER_X = 'X'
PLAYER_O = 'O'

class Board:
    def __init__(self):
        self.board = [[EMPTY_CELL for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]

    def update(self, row, col, player):
        if 0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE and self.is_empty(row, col):
            self.board[row][col] = player
            return True
        return False

    def is_empty(self, row, col):
        return self.board[row][col] == EMPTY_CELL

    def get_cell(self, row, col):
        return self.board[row][col]

    def get_all_cells(self):
        return [cell for row in self.board for cell in row]

    def __str__(self):
        board_str = ""
        for r_idx, row in enumerate(self.board):
            board_str += "|".join(row)
            if r_idx < BOARD_SIZE - 1:
                board_str += "\n-----\n"
        return board_str

# --- Game State Management and Core Logic ---

# Global state dictionary to be managed by this file
# This assumes game.py is responsible for state management as per instructions
game_state = {
    "board": None,
    "current_player": PLAYER_X,
    "game_over": False,
    "winner": None,
    "moves_count": 0
}

def initialize_game():
    """Initializes or resets the game state."""
    global game_state
    game_state["board"] = Board()
    game_state["current_player"] = PLAYER_X
    game_state["game_over"] = False
    game_state["winner"] = None
    game_state["moves_count"] = 0
    telemetry.log_state(game_state)

def get_current_player():
    """Returns the player whose turn it is."""
    return game_state["current_player"]

def make_move(row, col):
    """
    Attempts to make a move on the board.
    Returns True if the move was successful, False otherwise.
    """
    global game_state

    if game_state["game_over"]:
        return False

    if game_state["board"].update(row, col, game_state["current_player"]):
        game_state["moves_count"] += 1
        
        # Check for win or draw after a successful move
        if check_win(game_state["current_player"]):
            game_state["game_over"] = True
            game_state["winner"] = game_state["current_player"]
        elif check_draw():
            game_state["game_over"] = True
            game_state["winner"] = None # Explicitly no winner in a draw
        else:
            # Switch player if game is not over
            game_state["current_player"] = PLAYER_O if game_state["current_player"] == PLAYER_X else PLAYER_X
        
        telemetry.log_state(game_state)
        return True
    else:
        # Move failed (invalid cell or cell not empty)
        return False

def check_win(player):
    """
    Checks if the given player has won the game.
    """
    board_instance = game_state["board"]

    # Check rows
    for r in range(BOARD_SIZE):
        if all(board_instance.get_cell(r, c) == player for c in range(BOARD_SIZE)):
            return True

    # Check columns
    for c in range(BOARD_SIZE):
        if all(board_instance.get_cell(r, c) == player for r in range(BOARD_SIZE)):
            return True

    # Check diagonals
    if all(board_instance.get_cell(i, i) == player for i in range(BOARD_SIZE)):
        return True
    if all(board_instance.get_cell(i, BOARD_SIZE - 1 - i) == player for i in range(BOARD_SIZE)):
        return True

    return False

def check_draw():
    """
    Checks if the game is a draw.
    A draw occurs when the board is full and no player has won.
    """
    if game_state["game_over"]: # If game is already over (win), it's not a draw
        return False
        
    # Check if all cells are filled
    if game_state["moves_count"] == BOARD_SIZE * BOARD_SIZE:
        # If board is full and no one has won (check_win would have returned True if someone won)
        return True
    
    return False

# Initialize the game state when the module is first imported
initialize_game()