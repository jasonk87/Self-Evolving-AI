import snake
import food
import display
import utils

def main():
    # Initialize game state
    game_state = utils.initialize_game_state()

    # Run the game loop
    while not game_state['game_over']:
        # Handle input
        event = utils.get_event()
        if event:
            snake.move(game_state['snake'], event)

        # Update game state
        snake.update(game_state)
        food.update(game_state)

        # Render the game
        display.render(game_state)

        # Check for game over
        if game_state['game_over']:
            break

    print("Game Over!")

if __name__ == "__main__":
    main()