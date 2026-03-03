import json

class Board:
    def __init__(self, size=8):
        self.size = size
        self.board = [[' ' for _ in range(size)] for _ in range(size)]
        self.setup_board()
        self.state = {}
        self.update_state()

    def setup_board(self):
        # Initialize black pieces
        for row in range(3):
            for col in range((row + 1) % 2, self.size, 2):
                self.board[row][col] = 'b'

        # Initialize red pieces
        for row in range(self.size - 3, self.size):
            for col in range((row + 1) % 2, self.size, 2):
                self.board[row][col] = 'r'

    def display_board(self):
        for row in range(self.size):
            print(str(row) + " " + ' '.join(self.board[row]))
        print("  " + ' '.join([str(i) for i in range(self.size)]))

    def move_piece(self, start_row, start_col, end_row, end_col):
        if not self.is_valid_move(start_row, start_col, end_row, end_col):
            return False

        piece = self.board[start_row][start_col]
        self.board[start_row][start_col] = ' '
        self.board[end_row][end_col] = piece

        # Check for jumps and remove jumped pieces (basic implementation)
        if abs(end_row - start_row) == 2:
            jumped_row = (start_row + end_row) // 2
            jumped_col = (start_col + end_col) // 2
            self.board[jumped_row][jumped_col] = ' '

        self.update_state()
        return True

    def is_valid_move(self, start_row, start_col, end_row, end_col):
        if not (0 <= start_row < self.size and 0 <= start_col < self.size and
                0 <= end_row < self.size and 0 <= end_col < self.size):
            return False

        if self.board[start_row][start_col] == ' ':
            return False

        if self.board[end_row][end_col] != ' ':
            return False

        # Basic move validation (diagonal movement)
        if abs(end_row - start_row) != abs(end_col - start_col):
            return False

        # Further validation logic can be added here (piece color, direction, jumps, etc.)

        return True

    def update_state(self):
        self.state = {
            'board': self.board,
            'size': self.size
        }
        self.save_state()

    def save_state(self):
        try:
            with open('telemetry.json', 'w') as f:
                json.dump(self.state, f)
        except Exception as e:
            print(f"Error saving state: {e}")

    def load_state(self):
         try:
            with open('telemetry.json', 'r') as f:
                self.state = json.load(f)
                self.board = self.state['board']
                self.size = self.state['size']
         except FileNotFoundError:
            print("No previous state found. Starting a new game.")
         except Exception as e:
            print(f"Error loading state: {e}")