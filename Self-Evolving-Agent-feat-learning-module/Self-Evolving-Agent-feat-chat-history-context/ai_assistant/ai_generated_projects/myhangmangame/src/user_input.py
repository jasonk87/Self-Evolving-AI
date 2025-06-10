def handle_letter_input(letter):
    if len(letter) != 1:
        return False
    elif not letter.isalpha():
        return False
    else:
        return True

def check_for_win_conditions(word, guessed_letters):
    for letter in word:
        if letter not in guessed_letters:
            return False
    return True

def generate_error_message(error_type):
    error_messages = {
        'invalid_input': "Please enter a single alphabetic character.",
        'already_guessed': "You have already guessed this letter. Please try again.",
        'win_condition_met': " Congratulations, you won!"
    }
    return error_messages.get(error_type, "An unknown error occurred.")

def get_user_letter():
    while True:
        user_input = input("Please enter a single alphabetic character: ")
        if handle_letter_input(user_input):
            return user_input
        else:
            print(generate_error_message('invalid_input'))

def update_game_state(word, guessed_letters, correct_guesses):
    for letter in word:
        if letter not in guessed_letters:
            correct_guesses.append(letter)
    return guessed_letters, correct_guesses

def check_win_condition(game_state, word):
    return check_for_win_conditions(word, game_state[0])

def display_error_message(error_type):
    error_messages = {
        'invalid_input': "Please enter a single alphabetic character.",
        'already_guessed': "You have already guessed this letter. Please try again.",
        'win_condition_met': " Congratulations, you won!"
    }
    print(error_messages.get(error_type, "An unknown error occurred."))

def main():
    word = input("Please enter the word to guess: ")
    guessed_letters = []
    correct_guesses = []
    game_state = update_game_state(word, guessed_letters, correct_guesses)
    while True:
        user_letter = get_user_letter()
        if check_win_condition(game_state, word):
            display_error_message('win_condition_met')
            break
        else:
            updated_game_state = update_game_state(word, guessed_letters, correct_guesses)
            game_state = updated_game_state