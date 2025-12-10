import json
from board import Board
from player import Player
from ai import AI

def write_telemetry(data, filename="telemetry.json"):
    """Writes game state to a telemetry file."""
    with open(filename, 'w') as f:
        json.dump(data, f, indent=4)

def main():
    """Initializes and runs the checkers game."""

    board = Board()
    player = Player("Player", "white")
    ai = AI("AI", "black")

    current_player = player
    game_over = False

    # Initialize telemetry data
    telemetry_data = {
        "moves": [],
        "winner": None,
        "board_history": []
    }

    write_telemetry(telemetry_data)

    while not game_over:
        board.display()
        telemetry_data["board_history"].append(board.board)
        write_telemetry(telemetry_data)

        if current_player == player:
            print(f"{player.name}'s turn ({player.color})")
            move = player.get_move(board)
        else:
            print(f"{ai.name}'s turn ({ai.color})")
            move = ai.get_move(board)

        if move:
            start_row, start_col, end_row, end_col = move
            try:
                board.move_piece(start_row, start_col, end_row, end_col, current_player.color)
                telemetry_data["moves"].append({
                        "player": current_player.name,
                        "start": (start_row, start_col),
                        "end": (end_row, end_col)
                    })
                write_telemetry(telemetry_data)

            except ValueError as e:
                print(e)
                continue #restart the loop to get the move again from the same player

            if board.check_win(current_player.color):
                print(f"{current_player.name} wins!")
                game_over = True
                telemetry_data["winner"] = current_player.name
                write_telemetry(telemetry_data)
                break

            # Switch players
            current_player = ai if current_player == player else player
        else:
            print("Invalid move. Try again.")

    print("Game Over")

if __name__ == "__main__":
    main()