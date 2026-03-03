import json
from board import Board

class Player:
    def __init__(self, color):
        self.color = color

    def get_player_move(self, board):
        """
        Gets a move from the player and validates it.
        """
        while True:
            try:
                start_row = int(input("Enter the row of the piece you want to move (0-7): "))
                start_col = int(input("Enter the column of the piece you want to move (0-7): "))
                end_row = int(input("Enter the row where you want to move the piece (0-7): "))
                end_col = int(input("Enter the column where you want to move the piece (0-7): "))

                if not (0 <= start_row <= 7 and 0 <= start_col <= 7 and 0 <= end_row <= 7 and 0 <= end_col <= 7):
                    print("Invalid input. Row and column numbers must be between 0 and 7.")
                    continue

                if board.board[start_row][start_col] is None or board.board[start_row][start_col].color != self.color:
                    print("Invalid move. You must select one of your own pieces.")
                    continue

                if board.is_valid_move(start_row, start_col, end_row, end_col, self.color):
                    return (start_row, start_col, end_row, end_col)
                else:
                    print("Invalid move. Please check the rules of checkers.")

            except ValueError:
                print("Invalid input. Please enter integers.")
            except Exception as e:
                print(f"An unexpected error occurred: {e}")


if __name__ == '__main__':
    # Example usage
    board = Board()
    player = Player("black")  # Example: Player is black

    board.display_board()

    move = player.get_player_move(board)
    print(f"Player's move: {move}")

    # Example of making the move (assuming board.move_piece exists)
    start_row, start_col, end_row, end_col = move
    board.move_piece(start_row, start_col, end_row, end_col)

    board.display_board()