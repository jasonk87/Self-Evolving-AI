def handle_input():
    """
    Handles user input to update the snake's direction.
    """
    global direction
    if direction != "right" and key_press == "right":
        direction = "right"
    elif direction != "left" and key_press == "left":
        direction = "left"
    elif direction != "up" and key_press == "up":
        direction = "up"
    elif direction != "down" and key_press == "down":
        direction = "down"

def check_collision(snake_body, food_position):
    """
    Checks if the snake has collided with the walls or itself.
    """
    x, y = snake_body[0]
    width = game_width
    height = game_height

    if x < 0 or x >= width or y < 0 or y >= height:
        return True

    for i in range(1, len(snake_body)):
        if snake_body[i] == snake_body[0]:
            return True

    return False

def get_key_press():
    """
    Simulates key press detection for testing purposes.
    """
    global key_press
    key_press = None