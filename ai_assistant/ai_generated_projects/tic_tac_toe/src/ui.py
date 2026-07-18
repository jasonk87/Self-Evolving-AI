def display_board(board):
    """
    Prints the Tic Tac Toe board to the console.

    Args:
        board (list): A list of 9 elements representing the board state.
                      Each element can be 'X', 'O', or ' ' for an empty cell.
    """
    print("-------------")
    for i in range(0, 9, 3):
        print(f"| {board[i]} | {board[i+1]} | {board[i+2]} |")
        if i < 6:
            print("-------------")
    print("-------------")

def prompt_for_move(player, board):
    """
    Prompts the current player for their move and validates the input.

    Args:
        player (str): The current player ('X' or 'O').
        board (list): The current state of the board.

    Returns:
        int: The 0-indexed position (0-8) of the player's chosen move.
    """
    while True:
        try:
            move_str = input(f"Player {player}, enter your move (1-9): ")
            move_num = int(move_str)
            if 1 <= move_num <= 9:
                index = move_num - 1
                if board[index] == ' ':
                    return index
                else:
                    print("That cell is already occupied. Please choose another.")
            else:
                print("Invalid move. Please enter a number between 1 and 9.")
        except ValueError:
            print("Invalid input. Please enter a number.")
        except IndexError: # This case is covered by 1 <= move_num <= 9, but for robustness.
            print("Invalid input. Please enter a number between 1 and 9.")

def display_message(message):
    """
    Displays a message to the user.

    Args:
        message (str): The message to display.
    """
    print(message)