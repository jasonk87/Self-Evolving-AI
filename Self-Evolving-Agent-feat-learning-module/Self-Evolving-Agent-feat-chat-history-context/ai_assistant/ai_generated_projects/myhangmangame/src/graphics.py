from PIL import Image, ImageDraw

def generate_hangman_image(incorrect_guesses):
    # Create a new image with white background
    img = Image.new('RGB', (200, 200), color=(255, 255, 255))
    
    # Define the hangman parts and their corresponding incorrect guesses
    hangman_parts = {
        'head': 1,
        'body': 2,
        'left_arm': 3,
        'right_arm': 4,
        'left_leg': 5,
        'right_leg': 6
    }
    
    # Draw each part of the hangman graphic based on the number of incorrect guesses
    for i, (part, guess_count) in enumerate(hangman_parts.items()):
        if guess_count <= incorrect_guesses:
            draw = ImageDraw.Draw(img)
            if part == 'head':
                x, y = 50, 50
                radius = 20
                draw.ellipse([(x - radius, y - radius), (x + radius, y + radius)], fill=(0, 0, 0))
            elif part == 'body':
                x, y = 75, 100
                width, height = 10, 10
                draw.rectangle([(x - width/2, y - height/2), (x + width/2, y + height/2)], fill=(0, 0, 0))
            elif part == 'left_arm':
                x, y = 50, 150
                width, height = 5, 20
                draw.line([(x, y - height), (x - width/2, y + height)], fill=(0, 0, 0))
            elif part == 'right_arm':
                x, y = 75, 150
                width, height = 5, 20
                draw.line([(x, y - height), (x + width/2, y + height)], fill=(0, 0, 0))
            elif part == 'left_leg':
                x, y = 50, 200
                width, height = 5, 10
                draw.line([(x, y - height), (x - width/2, y + height)], fill=(0, 0, 0))
            elif part == 'right_leg':
                x, y = 75, 200
                width, height = 5, 10
                draw.line([(x, y - height), (x + width/2, y + height)], fill=(0, 0, 0))
    return img

def draw_hangman_on_screen(img):
    # Display the generated image on the screen using Pygame
    from pygame import display
    display.set_mode((800, 600))
    display.set_caption('Hangman Game')
    screen = display.set_mode((800, 600))
    screen.blit(img, (0, 0))
    display.update()

# Example usage:
incorrect_guesses = 5
generated_img = generate_hangman_image(incorrect_guesses)
draw_hangman_on_screen(generated_img)