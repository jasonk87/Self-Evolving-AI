class Snake:
    """
    Represents the snake in the Snake game.
    Handles movement, growth, and collision detection.
    """

    def __init__(self, start_position, direction="right"):
        """
        Initializes the snake with a starting position and direction.

        Args:
            start_position (tuple): The initial (x, y) coordinates of the snake's head.
            direction (str): The initial direction of the snake ("up", "down", "left", "right").
        """
        self.body = [start_position]
        self.direction = direction
        self.head = start_position

    def move(self):
        """
        Updates the snake's position based on its current direction.
        """
        if self.direction == "up":
            self.head = (self.head[0], self.head[1] - 1)
        elif self.direction == "down":
            self.head = (self.head[0], self.head[1] + 1)
        elif self.direction == "left":
            self.head = (self.head[0] - 1, self.head[1])
        elif self.direction == "right":
            self.head = (self.head[0] + 1, self.head[1])

        self.body.insert(0, self.head)  # Add new head to the beginning of the body
        if len(self.body) > 1:
            self.body.pop()  # Remove the tail segment

    def grow(self):
        """
        Increases the length of the snake by adding a new segment to its tail.
        """
        self.body.insert(0, self.body[-1])

    def check_collision(self):
        """
        Checks if the snake has collided with the walls or itself.

        Returns:
            bool: True if a collision has occurred, False otherwise.
        """
        if (
            self.head[0] < 0
            or self.head[0] >= 10  # Assuming a 10x10 grid
            or self.head[1] < 0
            or self.head[1] >= 10
        ):
            return True

        for i in range(len(self.body)):
            if self.body[i] == self.head:
                return True

        return False

    def reset(self):
        """
        Resets the snake's position and length to their initial values.
        """
        self.body = [self.head]
        self.direction = "right"