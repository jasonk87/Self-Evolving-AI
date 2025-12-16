import json
from board import Board

class Rules:
    def __init__(self, board):
        """
        Initializes the Rules object with a given board.
        """
        self.board = board

    def is_valid_move(self, start, end):
        """
        Checks if a move from start to end is valid.
        """
        if not self.board.is_within_bounds(start) or not self.board.is_within_bounds(end):
            return False

        if self.board.get_piece(end) is not None:
            return False

        piece = self.board.get_piece(start)
        if piece is None:
            return False

        color = piece.color
        is_king = piece.is_king

        row_diff = end[0] - start[0]
        col_diff = end[1] - start[1]

        if abs(row_diff) == 1 and abs(col_diff) == 1:
            if is_king:
                return True
            if color == 'red' and row_diff == 1:
                return True
            if color == 'black' and row_diff == -1:
                return True
        elif abs(row_diff) == 2 and abs(col_diff) == 2:
            # Check for jump
            jumped_row = (start[0] + end[0]) // 2
            jumped_col = (start[1] + end[1]) // 2
            jumped_piece = self.board.get_piece((jumped_row, jumped_col))

            if jumped_piece is not None and jumped_piece.color != color:
                return True

        return False

    def get_possible_moves(self, start):
        """
        Returns a list of possible moves for a piece at the given start position.
        """
        possible_moves = []
        piece = self.board.get_piece(start)
        if piece is None:
            return possible_moves

        row, col = start
        color = piece.color
        is_king = piece.is_king

        # Define possible move directions
        directions = []
        if color == 'red' or is_king:
            directions.append((1, 1))  # Down-right
            directions.append((1, -1)) # Down-left
        if color == 'black' or is_king:
            directions.append((-1, 1)) # Up-right
            directions.append((-1, -1))# Up-left

        for dr, dc in directions:
            # Check for regular moves
            new_row, new_col = row + dr, col + dc
            if self.board.is_within_bounds((new_row, new_col)) and self.board.get_piece((new_row, new_col)) is None:
                possible_moves.append((new_row, new_col))

            # Check for jumps
            jumped_row, jumped_col = row + dr, col + dc
            new_row, new_col = row + 2 * dr, col + 2 * dc

            if self.board.is_within_bounds((new_row, new_col)) and self.board.get_piece((new_row, new_col)) is None:
                jumped_piece = self.board.get_piece((jumped_row, jumped_col))
                if jumped_piece is not None and jumped_piece.color != color:
                    possible_moves.append((new_row, new_col))

        return possible_moves

    def perform_move(self, start, end):
        """
        Performs a move from start to end.  Assumes the move is valid.
        """
        piece = self.board.get_piece(start)
        self.board.set_piece(end, piece)
        self.board.set_piece(start, None)

        # Check for jump and remove jumped piece
        if abs(end[0] - start[0]) == 2:
            jumped_row = (start[0] + end[0]) // 2
            jumped_col = (start[1] + end[1]) // 2
            self.board.set_piece((jumped_row, jumped_col), None)

        # Check for king promotion
        if piece.color == 'red' and end[0] == self.board.rows - 1:
            piece.is_king = True
        elif piece.color == 'black' and end[0] == 0:
            piece.is_king = True

        self.board.update_board_state()  # Update the board state string
        self.save_game_state()

    def check_game_over(self):
        """
        Checks if the game is over.  Returns the winning color or None if the game is not over.
        """
        red_pieces = 0
        black_pieces = 0
        for row in range(self.board.rows):
            for col in range(self.board.cols):
                piece = self.board.get_piece((row, col))
                if piece is not None:
                    if piece.color == 'red':
                        red_pieces += 1
                    elif piece.color == 'black':
                        black_pieces += 1

        if red_pieces == 0:
            return 'black'
        if black_pieces == 0:
            return 'red'

        # Check if either player has any valid moves
        red_can_move = False
        black_can_move = False
        for row in range(self.board.rows):
            for col in range(self.board.cols):
                piece = self.board.get_piece((row, col))
                if piece is not None:
                    if piece.color == 'red' and self.get_possible_moves((row, col)):
                        red_can_move = True
                    elif piece.color == 'black' and self.get_possible_moves((row, col)):
                        black_can_move = True

        if not red_can_move:
            return 'black'
        if not black_can_move:
            return 'red'

        return None

    def save_game_state(self):
        """
        Saves the current game state to telemetry.json.
        """
        game_state = {
            "board": self.board.board_state,
            "rows": self.board.rows,
            "cols": self.board.cols,
            "turn": self.board.turn
        }
        try:
            with open("telemetry.json", "w") as f:
                json.dump(game_state, f)
        except Exception as e:
            print(f"Error saving game state: {e}")

if __name__ == '__main__':
    # Example usage
    board = Board(8, 8)
    rules = Rules(board)

    # Initial board setup (example)
    board.setup_board()
    print(board.board_state)

    # Example move
    start = (2, 0)
    end = (3, 1)

    if rules.is_valid_move(start, end):
        rules.perform_move(start, end)
        print(board.board_state)
    else:
        print("Invalid move")

    # Check for game over
    winner = rules.check_game_over()
    if winner:
        print(f"Game over! {winner} wins!")
    else:
        print("Game is not over.")