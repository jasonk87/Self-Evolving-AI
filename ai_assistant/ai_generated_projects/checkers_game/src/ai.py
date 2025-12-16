import board
import random
import copy

class AI:
    def __init__(self, color, depth=3):
        self.color = color
        self.depth = depth

    def get_ai_move(self, board_state):
        """
        Gets the best move for the AI using the minimax algorithm.
        """
        best_move = self.minimax(board_state, self.depth, self.color)[1]
        return best_move

    def minimax(self, board_state, depth, maximizing_player):
        """
        Minimax algorithm to find the best move.
        """
        if depth == 0 or board.is_game_over(board_state):
            return self.evaluation_function(board_state, self.color), None

        possible_moves = board.get_valid_moves(board_state, maximizing_player)

        if not possible_moves:
            # No valid moves, return a low score if maximizing, high if minimizing
            if maximizing_player == self.color:
                return float('-inf'), None
            else:
                return float('inf'), None

        if maximizing_player == self.color:
            best_score = float('-inf')
            best_move = None
            for move in possible_moves:
                new_board_state = board.apply_move(copy.deepcopy(board_state), move)
                score = self.minimax(new_board_state, depth - 1, board.opponent_color(maximizing_player))[0]
                if score > best_score:
                    best_score = score
                    best_move = move
            return best_score, best_move
        else:
            best_score = float('inf')
            best_move = None
            for move in possible_moves:
                new_board_state = board.apply_move(copy.deepcopy(board_state), move)
                score = self.minimax(new_board_state, depth - 1, self.color)[0]
                if score < best_score:
                    best_score = score
                    best_move = move
            return best_score, best_move

    def evaluation_function(self, board_state, ai_color):
        """
        Evaluates the board state for the AI.
        """
        ai_pieces = 0
        opponent_pieces = 0
        ai_kings = 0
        opponent_kings = 0

        for row in board_state:
            for piece in row:
                if piece == ai_color:
                    ai_pieces += 1
                elif piece == board.opponent_color(ai_color):
                    opponent_pieces += 1
                elif piece == board.king_piece(ai_color):
                    ai_kings += 1
                elif piece == board.king_piece(board.opponent_color(ai_color)):
                    opponent_kings += 1

        # Simple evaluation: piece difference + king difference
        score = (ai_pieces - opponent_pieces) + 0.5 * (ai_kings - opponent_kings)

        return score