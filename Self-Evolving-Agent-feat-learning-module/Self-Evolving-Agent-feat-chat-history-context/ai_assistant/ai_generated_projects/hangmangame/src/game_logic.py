import random

def select_word(word_list):
    """Selects a random word from the word list."""
    if not word_list:
        return None  # Handle empty word list
    return random.choice(word_list)

def check_guess(word, guess, displayed_word, incorrect_guesses):
    """Checks if the user's guess is correct and updates the game state."""
    if guess in word:
        displayed_word = displayed_word.replace(guess, guess, 1)
        return displayed_word, incorrect_guesses
    else:
        incorrect_guesses += 1
        return displayed_word, incorrect_guesses

def update_game_state(word, displayed_word, incorrect_guesses, guessed_letters):
    """Updates the game state, displaying the word and showing guessed letters."""
    if incorrect_guesses >= 6:
        return "You lost!"
    if "_" not in displayed_word:
        return "You won!"
    return displayed_word, incorrect_guesses, guessed_letters

def determine_winner(word, displayed_word, incorrect_guesses):
    """Checks if the user has guessed the word correctly or if they've run out of attempts."""
    if "_" not in displayed_word:
        return "You won!"
    if incorrect_guesses >= 6:
        return "You lost!"
    return None