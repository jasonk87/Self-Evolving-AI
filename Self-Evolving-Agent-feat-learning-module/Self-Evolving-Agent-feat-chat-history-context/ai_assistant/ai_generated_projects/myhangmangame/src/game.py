import random
import os

word_list = ['apple', 'banana', 'cherry', 'date', 'elderberry']

def handle_user_input(user_input):
    if len(user_input) == 1:
        return user_input.lower()
    elif len(user_input) <= len(word_list[0]):
        return ''.join([user_input])
    else:
        print('Invalid input. Please enter a single letter or a whole word.')
        return None

def update_game_state(word_to_guess, guessed_letters, incorrect_guesses):
    for letter in word_to_guess:
        if letter not in guessed_letters:
            incorrect_guesses.append(letter)
    return word_to_guess, guessed_letters, incorrect_guesses

def display_hangman(incorrect_guesses):
    stages = [  # final state: head, torso, both arms, and both legs
                """
                   --------
                   |      |
                   |      O
                   |     \\|/
                   |      |
                   |     / \\
                   -
                """,
                # head, torso, both arms, and one leg
                """
                   --------
                   |      |
                   |      O
                   |     \\|/
                   |      |
                   |     / 
                   -
                """,
                # head, torso, and both arms
                """
                   --------
                   |      |
                   |      O
                   |     \\|/
                   |      |
                   |      
                   -
                """,
                # head, torso, and one arm
                """
                   --------
                   |      |
                   |      O
                   |     \\|
                   |      |
                   |     
                   -
                """,
                # head and torso
                """
                   --------
                   |      |
                   |      O
                   |      |
                   |      |
                   |     
                   -
                """,
                # head
                """
                   --------
                   |      |
                   |      O
                   |    
                   |      
                   |     
                   -
                """,
                # initial empty state
                """
                   --------
                   |      |
                   |      
                   |    
                   |      
                   |     
                   -
                """
    ]
    return stages[len(incorrect_guesses)-1]

def main():
    word_to_guess = random.choice(word_list)
    guessed_letters = []
    incorrect_guesses = []

    while len(incorrect_guesses) < 6:
        user_input = input("Guess a letter or a whole word: ")
        user_input = handle_user_input(user_input)

        if user_input is None:
            continue

        word_to_guess, guessed_letters, incorrect_guesses = update_game_state(word_to_guess, guessed_letters, incorrect_guesses)
        print(' '.join([letter if letter in guessed_letters else display_hangman(incorrect_guesses)[7+i] for i, letter in enumerate(word_to_guess)]))

    if len(incorrect_guesses) < 6:
        print(f'Congratulations! You won. The word was {word_to_guess}.')
    else:
        print('Game over. The word was ' + word_to_guess)

if __name__ == "__main__":
    main()