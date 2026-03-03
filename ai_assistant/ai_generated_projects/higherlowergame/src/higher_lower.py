import random
import json
from telemetry import update_telemetry

def generate_secret_number():
    """Generates a random integer between 1 and 100 (inclusive)."""
    return random.randint(1, 100)

def get_user_guess():
    """Prompts the user for a guess and validates the input."""
    while True:
        try:
            guess = int(input("Enter your guess (between 1 and 100): "))
            if 1 <= guess <= 100:
                return guess
            else:
                print("Please enter a number between 1 and 100.")
        except ValueError:
            print("Invalid input. Please enter a number.")

def check_guess(guess, secret_number):
    """Checks the user's guess against the secret number."""
    if guess < secret_number:
        return "higher"
    elif guess > secret_number:
        return "lower"
    else:
        return "correct"

def play_game():
    """Plays the higher lower number game."""
    secret_number = generate_secret_number()
    attempts = 0
    telemetry_data = {"games_played": 0, "wins": 0, "losses": 0, "total_attempts": 0}

    try:
        with open("telemetry.json", "r") as f:
            telemetry_data = json.load(f)
    except FileNotFoundError:
        pass

    telemetry_data["games_played"] += 1
    update_telemetry(telemetry_data)

    while True:
        guess = get_user_guess()
        attempts += 1
        result = check_guess(guess, secret_number)

        if result == "correct":
            print(f"Congratulations! You guessed the number {secret_number} in {attempts} attempts.")
            telemetry_data["wins"] += 1
            telemetry_data["total_attempts"] += attempts
            update_telemetry(telemetry_data)
            break
        else:
            print(f"Too {result}. Try again.")

    with open("telemetry.json", "w") as f:
        json.dump(telemetry_data, f)

if __name__ == "__main__":
    play_game()