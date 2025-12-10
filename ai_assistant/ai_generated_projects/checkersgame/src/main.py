import json
from board import Board
from player import Player
from rules import Rules
from telemetry import Telemetry

class CheckersGame:
    def __init__(self):
        self.board = Board()
        self.player1 = Player("Player 1", "W")
        self.player2 = Player("Player 2", "B")
        self.rules = Rules(self.board)
        self.current_player = self.player1
        self.telemetry = Telemetry()
        self.game_over = False
        self.state = {}

    def initialize_game(self):
        self.board.setup_board()
        self.update_state()
        self.telemetry.write_telemetry(self.state)

    def switch_player(self):
        if self.current_player == self.player1:
            self.current_player = self.player2
        else:
            self.current_player = self.player1

    def update_state(self):
        self.state = {
            "board": self.board.board,
            "current_player": self.current_player.name,
            "player1_pieces": self.player1.pieces,
            "player2_pieces": self.player2.pieces,
            "game_over": self.game_over
        }

    def handle_move(self, start_row, start_col, end_row, end_col):
        if not self.rules.is_valid_move(start_row, start_col, end_row, end_col, self.current_player.color):
            print("Invalid move.")
            return False

        self.board.move_piece(start_row, start_col, end_row, end_col)

        if self.rules.is_capture(start_row, start_col, end_row, end_col):
            captured_row = (start_row + end_row) // 2
            captured_col = (start_col + end_col) // 2
            captured_piece_color = self.board.board[captured_row][captured_col]

            if captured_piece_color == self.player1.color:
                self.player1.pieces -= 1
            else:
                self.player2.pieces -= 1

            self.board.remove_piece(captured_row, captured_col)

            if self.player1.pieces == 0 or self.player2.pieces == 0:
                self.game_over = True
                self.update_state()
                self.telemetry.write_telemetry(self.state)
                return True

        self.update_state()
        self.telemetry.write_telemetry(self.state)
        return True

    def game_loop(self):
        while not self.game_over:
            self.board.print_board()
            print(f"{self.current_player.name}'s turn ({self.current_player.color})")

            try:
                start_row = int(input("Enter start row: "))
                start_col = int(input("Enter start column: "))
                end_row = int(input("Enter end row: "))
                end_col = int(input("Enter end column: "))
            except ValueError:
                print("Invalid input. Please enter numbers.")
                continue

            if self.handle_move(start_row, start_col, end_row, end_col):
                self.switch_player()

        if self.player1.pieces == 0:
            print("Player 2 wins!")
        else:
            print("Player 1 wins!")

if __name__ == "__main__":
    game = CheckersGame()
    game.initialize_game()
    game.game_loop()