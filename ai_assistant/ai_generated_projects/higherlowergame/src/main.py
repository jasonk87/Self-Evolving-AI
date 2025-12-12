import random
import json

def play_game():
    """Plays a round of the higher lower number game."""

    secret_number = random.randint(1, 100)
    attempts = 0

    print("Welcome to the Higher Lower Game!")
    print("I'm thinking of a number between 1 and 100.")

    while True:
        try:
            guess = int(input("Take a guess: "))
            attempts += 1

            if guess < secret_number:
                print("Too low!")
            elif guess > secret_number:
                print("Too high!")
            else:
                print(f"Congratulations! You guessed the number in {attempts} attempts.")
                break
        except ValueError:
            print("Invalid input. Please enter a number.")

    # Save game state to telemetry.json (minimal state for this example)
    game_state = {"attempts": attempts, "secret_number": secret_number, "won": True}
    with open("telemetry.json", "w") as f:
        json.dump(game_state, f)

if __name__ == "__main__":
    play_game()