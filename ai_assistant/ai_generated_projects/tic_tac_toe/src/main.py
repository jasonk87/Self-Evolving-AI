import json
import game
import ui
import telemetry

TELEMETRY_FILE = 'telemetry.json'

def initialize_game():
    """Initializes the game state."""
    initial_state = {
        'board': game.create_board(),
        'current_player': 'X',
        'winner': None,
        'game_over': False,
        'message': ''
    }
    return initial_state

def run_game_loop(game_state):
    """Runs the main game loop until the game is over."""
    while not game_state['game_over']:
        ui.display_board(game_state['board'])
        ui.display_message(f"It's Player {game_state['current_player']}'s turn.")

        valid_move_made = False
        while not valid_move_made:
            try:
                row, col = ui.get_player_move(game_state['current_player'])
                if game.is_valid_move(game_state['board'], row, col):
                    game.update_board(game_state['board'], row, col, game_state['current_player'])
                    valid_move_made = True
                else:
                    game_state['message'] = "Invalid move. That spot is already taken or out of bounds. Try again."
                    ui.display_message(game_state['message'])
            except ValueError as e:
                game_state['message'] = str(e)
                ui.display_message(game_state['message'])
            except IndexError:
                game_state['message'] = "Invalid input. Please enter row and column numbers (e.g., 1 2)."
                ui.display_message(game_state['message'])

        # Check for win or draw after a valid move
        if game.check_win(game_state['board'], game_state['current_player']):
            game_state['winner'] = game_state['current_player']
            game_state['game_over'] = True
            game_state['message'] = f"Player {game_state['current_player']} wins!"
        elif game.check_draw(game_state['board']):
            game_state['game_over'] = True
            game_state['message'] = "It's a draw!"
        else:
            # Switch player if game is not over
            game_state['current_player'] = game.switch_player(game_state['current_player'])

        # Save state after every significant change
        telemetry.save_state(game_state, TELEMETRY_FILE)

    # Game is over, display final board and result
    ui.display_board(game_state['board'])
    ui.display_result(game_state['winner'], game_state['message'])

def start_new_game():
    """Initializes and runs a new game, handling play again logic."""
    while True:
        game_state = initialize_game()
        telemetry.save_state(game_state, TELEMETRY_FILE) # Save initial state
        run_game_loop(game_state)

        if not ui.ask_play_again():
            break

if __name__ == "__main__":
    start_new_game()
