import random
from abc import ABC, abstractmethod

class Player(ABC):
    def __init__(self, name, color):
        self.name = name
        self.color = color
        self.pieces = 12

    @abstractmethod
    def get_move(self, board):
        pass

    def __str__(self):
        return f"{self.name} ({self.color})"


class HumanPlayer(Player):
    def __init__(self, name, color):
        super().__init__(name, color)

    def get_move(self, board):
        while True:
            try:
                start_row = int(input("Enter the row of the piece you want to move (0-7): "))
                start_col = int(input("Enter the column of the piece you want to move (0-7): "))
                end_row = int(input("Enter the row where you want to move the piece (0-7): "))
                end_col = int(input("Enter the column where you want to move the piece (0-7): "))

                if not (0 <= start_row <= 7 and 0 <= start_col <= 7 and 0 <= end_row <= 7 and 0 <= end_col <= 7):
                    print("Invalid input. Row and column numbers must be between 0 and 7.")
                    continue

                start_pos = (start_row, start_col)
                end_pos = (end_row, end_col)

                if board.is_valid_move(start_pos, end_pos, self.color):
                    return start_pos, end_pos
                else:
                    print("Invalid move. Please try again.")
            except ValueError:
                print("Invalid input. Please enter numbers.")
            except Exception as e:
                print(f"An unexpected error occurred: {e}")
                print("Please try again.")


class AIPlayer(Player):
    def __init__(self, color, difficulty="easy"):
        super().__init__("AI", color)
        self.difficulty = difficulty  # "easy", "medium", "hard"

    def get_move(self, board):
        valid_moves = board.get_valid_moves(self.color)

        if not valid_moves:
            return None, None  # No valid moves available

        if self.difficulty == "easy":
            start_pos, end_pos = random.choice(valid_moves)
            return start_pos, end_pos
        elif self.difficulty == "medium":
            # Implement a more sophisticated move selection strategy here
            # For example, prioritize capturing pieces
            captures = []
            non_captures = []
            for start_pos, end_pos in valid_moves:
                if board.is_capture_move(start_pos, end_pos):
                    captures.append((start_pos, end_pos))
                else:
                    non_captures.append((start_pos, end_pos))

            if captures:
                start_pos, end_pos = random.choice(captures)
            else:
                start_pos, end_pos = random.choice(non_captures)
            return start_pos, end_pos

        elif self.difficulty == "hard":
            # Implement a minimax or alpha-beta pruning algorithm here
            # For now, just act like medium
            captures = []
            non_captures = []
            for start_pos, end_pos in valid_moves:
                if board.is_capture_move(start_pos, end_pos):
                    captures.append((start_pos, end_pos))
                else:
                    non_captures.append((start_pos, end_pos))

            if captures:
                start_pos, end_pos = random.choice(captures)
            else:
                start_pos, end_pos = random.choice(non_captures)
            return start_pos, end_pos
        else:
            start_pos, end_pos = random.choice(valid_moves)
            return start_pos, end_pos