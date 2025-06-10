import random

class Food:
    def __init__(self, grid_width, grid_height):
        self.x = 0
        self.y = 0
        self.grid_width = grid_width
        self.grid_height = grid_height
        self.generate_food()

    def generate_food(self):
        self.x = random.randint(0, self.grid_width - 1)
        self.y = random.randint(0, self.grid_height - 1)

    def move_food(self):
        # Placeholder - This method will be called in the snake.py file
        pass

    def reset(self):
        self.generate_food()