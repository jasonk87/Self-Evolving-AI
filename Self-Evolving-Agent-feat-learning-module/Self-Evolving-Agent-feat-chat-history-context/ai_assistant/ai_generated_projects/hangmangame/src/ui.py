# ui.py
# Handles the user interface for the Hangman game.
# Assumes game_logic.py exists and provides the correct word when the game is over.

def render_word(hidden_word, guessed_letters):
    """
    Displays the hidden word with underscores for unguessed letters.
    """
    displayed_word = ""
    for letter in hidden_word:
        if letter in guessed_letters:
            displayed_word += letter + " "
        else:
            displayed_word += "_ "
    return displayed_word.strip()

def display_guesses(guessed_letters):
    """
    Displays the list of letters the user has correctly guessed.
    """
    if not guessed_letters:
        return "No letters guessed yet."
    else:
        return ", ".join(guessed_letters)

def update_ui(hidden_word, guessed_letters, game_over):
    """
    Orchestrates the updates to the UI based on the game logic's output.
    """
    if game_over:
        print("Game Over!")
        print(render_word(hidden_word, guessed_letters))
    else:
        print(render_word(hidden_word, guessed_letters))
        print("Guessed letters:", display_guesses(guessed_letters))

def handle_display_updates(game_logic_output):
    """
    Placeholder for future enhancements to handle UI updates.
    """
    pass