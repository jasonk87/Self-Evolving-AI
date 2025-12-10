import json

class Board:
    def __init__(self):
        self.board = [
            [None, 'b', None, 'b', None, 'b', None, 'b'],
            ['b', None, 'b', None, 'b', None, 'b', None],
            [None, 'b', None, 'b', None, 'b', None, 'b'],
            [None, None, None, None, None, None, None, None],
            [None, None, None, None, None, None, None, None],
            ['r', None, 'r', None, 'r', None, 'r', None],
            [None, 'r', None, 'r', None, 'r', None, 'r'],
            ['r', None, 'r', None, 'r', None, 'r', None]
        ]
        self.current_player = 'r'  # Red starts
        self.telemetry_file = "telemetry.json"
        self.save_state()

    def save_state(self):
        state = {
            "board": self.board,
            "current_player": self.current_player
        }
        try:
            with open(self.telemetry_file, 'w') as f:
                json.dump(state, f)
        except Exception as e:
            print(f"Error saving state to {self.telemetry_file}: {e}")

    def move_piece(self, start_row, start_col, end_row, end_col):
        if not self.is_valid_move(start_row, start_col, end_row, end_col):
            return False

        piece = self.board[start_row][start_col]
        self.board[start_row][start_col] = None
        self.board[end_row][end_col] = piece

        # Check for and handle jumps
        if abs(end_row - start_row) == 2:
            jumped_row = (start_row + end_row) // 2
            jumped_col = (start_col + end_col) // 2
            self.board[jumped_row][jumped_col] = None

        # Check for king promotion
        if piece == 'r' and end_row == 0:
            self.board[end_row][end_col] = 'R'  # Promote to red king
        elif piece == 'b' and end_row == 7:
            self.board[end_row][end_col] = 'B'  # Promote to black king

        self.current_player = 'b' if self.current_player == 'r' else 'r'
        self.save_state()
        return True

    def is_valid_move(self, start_row, start_col, end_row, end_col):
        if not (0 <= start_row < 8 and 0 <= start_col < 8 and 0 <= end_row < 8 and 0 <= end_col < 8):
            return False

        piece = self.board[start_row][start_col]
        if piece is None:
            return False

        if piece.lower() != self.current_player:
            return False

        if self.board[end_row][end_col] is not None:
            return False

        row_diff = end_row - start_row
        col_diff = end_col - start_col

        if piece == 'r':
            if row_diff == -1 and abs(col_diff) == 1:
                return True  # Regular move
            elif row_diff == -2 and abs(col_diff) == 2:
                jumped_row = (start_row + end_row) // 2
                jumped_col = (start_col + end_col) // 2
                jumped_piece = self.board[jumped_row][jumped_col]
                return jumped_piece is not None and jumped_piece.lower() == 'b'  # Jump
            else:
                return False
        elif piece == 'b':
            if row_diff == 1 and abs(col_diff) == 1:
                return True  # Regular move
            elif row_diff == 2 and abs(col_diff) == 2:
                jumped_row = (start_row + end_row) // 2
                jumped_col = (start_col + end_col) // 2
                jumped_piece = self.board[jumped_row][jumped_col]
                return jumped_piece is not None and jumped_piece.lower() == 'r'  # Jump
            else:
                return False
        elif piece == 'R': # Red King
            if abs(row_diff) == 1 and abs(col_diff) == 1:
                return True
            elif abs(row_diff) == 2 and abs(col_diff) == 2:
                jumped_row = (start_row + end_row) // 2
                jumped_col = (start_col + end_col) // 2
                jumped_piece = self.board[jumped_row][jumped_col]
                return jumped_piece is not None and jumped_piece.lower() == 'b'
            else:
                return False
        elif piece == 'B': # Black King
            if abs(row_diff) == 1 and abs(col_diff) == 1:
                return True
            elif abs(row_diff) == 2 and abs(col_diff) == 2:
                jumped_row = (start_row + end_row) // 2
                jumped_col = (start_col + end_col) // 2
                jumped_piece = self.board[jumped_row][jumped_col]
                return jumped_piece is not None and jumped_piece.lower() == 'r'
            else:
                return False

        return False

    def get_possible_moves(self, row, col):
        piece = self.board[row][col]
        if piece is None:
            return []

        moves = []
        if piece == 'r':
            possible_moves = [(row - 1, col - 1), (row - 1, col + 1)]
            possible_jumps = [(row - 2, col - 2), (row - 2, col + 2)]

            for r, c in possible_moves:
                if 0 <= r < 8 and 0 <= c < 8 and self.board[r][c] is None:
                    moves.append((r, c))

            for r, c in possible_jumps:
                if 0 <= r < 8 and 0 <= c < 8 and self.board[r][c] is None:
                    jumped_row = (row + r) // 2
                    jumped_col = (col + c) // 2
                    if 0 <= jumped_row < 8 and 0 <= jumped_col < 8 and self.board[jumped_row][jumped_col] is not None and self.board[jumped_row][jumped_col].lower() == 'b':
                        moves.append((r, c))

        elif piece == 'b':
            possible_moves = [(row + 1, col - 1), (row + 1, col + 1)]
            possible_jumps = [(row + 2, col - 2), (row + 2, col + 2)]

            for r, c in possible_moves:
                if 0 <= r < 8 and 0 <= c < 8 and self.board[r][c] is None:
                    moves.append((r, c))

            for r, c in possible_jumps:
                if 0 <= r < 8 and 0 <= c < 8 and self.board[r][c] is None:
                    jumped_row = (row + r) // 2
                    jumped_col = (col + c) // 2
                    if 0 <= jumped_row < 8 and 0 <= jumped_col < 8 and self.board[jumped_row][jumped_col] is not None and self.board[jumped_row][jumped_col].lower() == 'r':
                        moves.append((r, c))
        elif piece == 'R':
            possible_moves = [(row - 1, col - 1), (row - 1, col + 1), (row + 1, col - 1), (row + 1, col + 1)]
            possible_jumps = [(row - 2, col - 2), (row - 2, col + 2), (row + 2, col - 2), (row + 2, col + 2)]

            for r, c in possible_moves:
                if 0 <= r < 8 and 0 <= c < 8 and self.board[r][c] is None:
                    moves.append((r, c))

            for r, c in possible_jumps:
                if 0 <= r < 8 and 0 <= c < 8 and self.board[r][c] is None:
                    jumped_row = (row + r) // 2
                    jumped_col = (col + c) // 2
                    if 0 <= jumped_row < 8 and 0 <= jumped_col < 8 and self.board[jumped_row][jumped_col] is not None and self.board[jumped_row][jumped_col].lower() == 'b':
                        moves.append((r, c))
        elif piece == 'B':
            possible_moves = [(row - 1, col - 1), (row - 1, col + 1), (row + 1, col - 1), (row + 1, col + 1)]
            possible_jumps = [(row - 2, col - 2), (row - 2, col + 2), (row + 2, col - 2), (row + 2, col + 2)]

            for r, c in possible_moves:
                if 0 <= r < 8 and 0 <= c < 8 and self.board[r][c] is None:
                    moves.append((r, c))

            for r, c in possible_jumps:
                if 0 <= r < 8 and 0 <= c < 8 and self.board[r][c] is None:
                    jumped_row = (row + r) // 2
                    jumped_col = (col + c) // 2
                    if 0 <= jumped_row < 8 and 0 <= jumped_col < 8 and self.board[jumped_row][jumped_col] is not None and self.board[jumped_row][jumped_col].lower() == 'r':
                        moves.append((r, c))

        return moves

    def __str__(self):
        board_str = ""
        for row in self.board:
            board_str += str(row) + "\n"
        return board_str

if __name__ == '__main__':
    board = Board()
    print(board)

    # Example move
    if board.move_piece(5, 0, 4, 1):
        print("Move successful!")
        print(board)
    else:
        print("Invalid move.")

    # Example of getting possible moves
    possible_moves = board.get_possible_moves(4, 1)
    print(f"Possible moves for piece at (4, 1): {possible_moves}")