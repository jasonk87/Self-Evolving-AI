import json

class GameBoard:
    def __init__(self):
        self.board = [" "] * 9

    def display(self):
        print("-------------")
        for i in range(3):
            print(f"| {self.board[i*3]} | {self.board[i*3 + 1]} | {self.board[i*3 + 2]} |")
            print("-------------")

    def is_full(self):
        return " " not in self.board

    def make_move(self, position, player):
        if self.board[position] == " ":
            self.board[position] = player.marker
            return True
        return False

    def undo_move(self, position):
        self.board[position] = " "

class Player:
    def __init__(self, marker):
        self.marker = marker

class AIPlayer(Player):
    def __init__(self, marker):
        super().__init__(marker)

    def get_move(self, board):
        return minimax(board, self.marker)["position"]

def get_available_moves(board):
    return [i for i, spot in enumerate(board.board) if spot == " "]

def is_game_over(board):
    # Check rows
    for i in range(0, 9, 3):
        if board.board[i] == board.board[i+1] == board.board[i+2] != " ":
            return True
    # Check columns
    for i in range(3):
        if board.board[i] == board.board[i+3] == board.board[i+6] != " ":
            return True
    # Check diagonals
    if board.board[0] == board.board[4] == board.board[8] != " ":
        return True
    if board.board[2] == board.board[4] == board.board[6] != " ":
        return True
    # Check if board is full
    if board.is_full():
        return True
    return False

def evaluate_board(board, ai_marker, human_marker):
    # Check rows
    for i in range(0, 9, 3):
        if board.board[i] == board.board[i+1] == board.board[i+2] == ai_marker:
            return 1
        if board.board[i] == board.board[i+1] == board.board[i+2] == human_marker:
            return -1
    # Check columns
    for i in range(3):
        if board.board[i] == board.board[i+3] == board.board[i+6] == ai_marker:
            return 1
        if board.board[i] == board.board[i+3] == board.board[i+6] == human_marker:
            return -1
    # Check diagonals
    if board.board[0] == board.board[4] == board.board[8] == ai_marker:
        return 1
    if board.board[0] == board.board[4] == board.board[8] == human_marker:
        return -1
    if board.board[2] == board.board[4] == board.board[6] == ai_marker:
        return 1
    if board.board[2] == board.board[4] == board.board[6] == human_marker:
        return -1
    return 0

def minimax(board, player_marker):
    available_moves = get_available_moves(board)

    if is_game_over(board):
        if evaluate_board(board, ai_marker, human_marker) == 1:
            return {"position": None, "score": 1}
        elif evaluate_board(board, ai_marker, human_marker) == -1:
            return {"position": None, "score": -1}
        else:
            return {"position": None, "score": 0}

    if player_marker == ai_marker:
        best = {"position": None, "score": -2}
    else:
        best = {"position": None, "score": 2}

    for move in available_moves:
        board.make_move(move, Player(player_marker))
        if player_marker == ai_marker:
            score = minimax(board, human_marker)["score"]
            if score > best["score"]:
                best["score"] = score
                best["position"] = move
        else:
            score = minimax(board, ai_marker)["score"]
            if score < best["score"]:
                best["score"] = score
                best["position"] = move
        board.undo_move(move)

    return best

def play_game():
    global ai_marker, human_marker
    board = GameBoard()
    human_marker = input("Choose your marker (X or O): ").upper()
    while human_marker not in ["X", "O"]:
        human_marker = input("Invalid marker. Choose X or O: ").upper()

    ai_marker = "O" if human_marker == "X" else "X"
    human = Player(human_marker)
    ai = AIPlayer(ai_marker)

    first_player = input("Do you want to go first? (yes/no): ").lower()
    if first_player == "yes":
        current_player = human
    else:
        current_player = ai

    game_state = {
        "board": board.board,
        "human_marker": human.marker,
        "ai_marker": ai.marker,
        "current_player": "human" if current_player == human else "ai"
    }
    write_telemetry(game_state)

    while not is_game_over(board):
        board.display()
        if current_player == human:
            try:
                position = int(input(f"Enter your move (0-8): "))
                if not (0 <= position <= 8):
                    print("Invalid position. Please enter a number between 0 and 8.")
                    continue
                if board.make_move(position, human):
                    current_player = ai
                else:
                    print("That position is already taken. Try again.")
            except ValueError:
                print("Invalid input. Please enter a number.")
        else:
            print("AI is thinking...")
            position = ai.get_move(board)
            if board.make_move(position, ai):
                current_player = human
            else:
                print("AI made an invalid move. This should not happen.")
                break

        game_state["board"] = board.board
        game_state["current_player"] = "human" if current_player == human else "ai"
        write_telemetry(game_state)

    board.display()
    if evaluate_board(board, ai_marker, human_marker) == 1:
        print("AI wins!")
    elif evaluate_board(board, ai_marker, human_marker) == -1:
        print("You win!")
    else:
        print("It's a tie!")

def write_telemetry(game_state):
    with open("telemetry.json", "w") as f:
        json.dump(game_state, f)

if __name__ == "__main__":
    play_game()