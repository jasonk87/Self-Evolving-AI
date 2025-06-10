import pygame

def initialize_display(width, height):
    pygame.init()
    screen = pygame.display.set_mode((width, height))
    pygame.display.set_caption("Snake")
    return screen

def draw_snake(screen, snake_coordinates):
    for x, y in snake_coordinates:
        pygame.draw.rect(screen, (0, 0, 20, 20), pygame.Rect(x, y, 20, 20))

def draw_food(screen, food_x, food_y):
    pygame.draw.rect(screen, (255, 0, 0), pygame.Rect(food_x, food_y, 20, 20))

def draw_boundaries(screen):
    pygame.draw.rect(screen, (0, 0, 20, 20), pygame.Rect(0, 0, 20, 20))
    pygame.draw.rect(screen, (0, 0, 20, 20), pygame.Rect(width - 20, 0, 20, 20))
    pygame.draw.rect(screen, (0, 0, 20, 20), pygame.Rect(0, height - 20, 20, 20))
    pygame.draw.rect(screen, (0, 0, 20, 20), pygame.Rect(width - 20, height - 20, 20, 20))

def update_display(screen, snake_coordinates, food_x, food_y):
    screen.fill((255, 255, 255))  # White background
    draw_snake(screen, snake_coordinates)
    draw_food(screen, food_x, food_y)
    draw_boundaries(screen)
    pygame.display.flip()