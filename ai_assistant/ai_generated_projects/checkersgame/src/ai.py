import board
import rules
import player
import random
import copy
import json

def evaluate_board(board_state, ai_player_color):
    """
    Evaluates the board state from the perspective of the AI player.
    A positive score indicates an advantage for the AI, while a negative score
    indicates an advantage for the opponent.

    Factors considered:
    - Material count (number of pieces)
    - King count
    - Piece advancement (closer to becoming a king)
    - Piece positioning (e.g., center control)

    Args:
        board_state: The current state of the board (a board.Board object).
        ai_player_color: The color of the AI player (e.g., 'red' or 'black').

    Returns:
        A numerical score representing the evaluation of the board state.
    """

    opponent_color = 'black' if ai_player_color == 'red' else 'red'
    ai_pieces = board_state.get_pieces(ai_player_color)
    opponent_pieces = board_state.get_pieces(opponent_color)

    ai_piece_count = len(ai_pieces)
    opponent_piece_count = len(opponent_pieces)

    ai_king_count = sum(1 for piece in ai_pieces if piece.is_king)
    opponent_king_count = sum(1 for piece in opponent_pieces if piece.is_king)

    # Material advantage
    material_score = (ai_piece_count - opponent_piece_count) * 100

    # King advantage
    king_score = (ai_king_count - opponent_king_count) * 150

    # Piece advancement (example: average row number)
    ai_advancement = sum(piece.row for piece in ai_pieces) / ai_piece_count if ai_piece_count > 0 else 0
    opponent_advancement = sum(piece.row for piece in opponent_pieces) / opponent_piece_count if opponent_piece_count > 0 else 0

    if ai_player_color == 'red':
        advancement_score = (opponent_advancement - ai_advancement) * 10
    else:
        advancement_score = (ai_advancement - opponent_advancement) * 10

    # Combine scores with weights
    total_score = material_score + king_score + advancement_score
    return total_score


def minimax(board_state, depth, maximizing_player, ai_player_color, alpha, beta):
    """
    Implements the Minimax algorithm with Alpha-Beta pruning.

    Args:
        board_state: The current state of the board (a board.Board object).
        depth: The remaining depth of the search tree.
        maximizing_player: True if the current player is the AI (maximizing player), False otherwise.
        ai_player_color: The color of the AI player.
        alpha: The best value that the maximizing player can guarantee at the current level or above.
        beta: The best value that the minimizing player can guarantee at the current level or above.

    Returns:
        A tuple: (score, best_move)
        - score: The minimax score for the current board state.
        - best_move: The best move found from this state (None if no move is available or depth is 0).
    """

    if depth == 0 or rules.is_game_over(board_state):
        return evaluate_board(board_state, ai_player_color), None

    if maximizing_player:
        max_eval = float('-inf')
        best_move = None
        possible_moves = rules.get_all_possible_moves(board_state, ai_player_color)

        if not possible_moves:
            return evaluate_board(board_state, ai_player_color), None

        for move in possible_moves:
            new_board_state = copy.deepcopy(board_state)
            rules.make_move(new_board_state, move)
            eval, _ = minimax(new_board_state, depth - 1, False, ai_player_color, alpha, beta)
            if eval > max_eval:
                max_eval = eval
                best_move = move
            alpha = max(alpha, eval)
            if beta <= alpha:
                break  # Beta cutoff
        return max_eval, best_move
    else:
        min_eval = float('inf')
        best_move = None
        opponent_color = 'black' if ai_player_color == 'red' else 'red'
        possible_moves = rules.get_all_possible_moves(board_state, opponent_color)

        if not possible_moves:
            return evaluate_board(board_state, ai_player_color), None

        for move in possible_moves:
            new_board_state = copy.deepcopy(board_state)
            rules.make_move(new_board_state, move)
            eval, _ = minimax(new_board_state, depth - 1, True, ai_player_color, alpha, beta)
            if eval < min_eval:
                min_eval = eval
                best_move = move
            beta = min(beta, eval)
            if beta <= alpha:
                break  # Alpha cutoff
        return min_eval, best_move


def get_ai_move(board_state, ai_player_color, depth=3):
    """
    Gets the best move for the AI player using the Minimax algorithm.

    Args:
        board_state: The current state of the board (a board.Board object).
        ai_player_color: The color of the AI player.
        depth: The depth of the Minimax search tree.

    Returns:
        A move (tuple of (row1, col1, row2, col2)) representing the best move for the AI.
        Returns None if no move is available.
    """
    _, best_move = minimax(board_state, depth, True, ai_player_color, float('-inf'), float('inf'))
    return best_move

if __name__ == '__main__':
    # Example usage:
    initial_board = board.Board()
    initial_board.initialize_board()
    ai_color = 'red'
    print("Initial Board:")
    initial_board.display_board()

    best_move = get_ai_move(initial_board, ai_color, depth=3)

    if best_move:
        print(f"AI ({ai_color}) move: {best_move}")
        rules.make_move(initial_board, best_move)
        print("Board after AI move:")
        initial_board.display_board()
    else:
        print("No possible moves for AI.")