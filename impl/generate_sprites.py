#!/usr/bin/env python3
"""Generate pre-rendered circle sprites for each subway line with perfectly centered text."""

from PIL import Image, ImageDraw, ImageFont
import os

# MTA subway line colors (official colors)
LINE_COLORS = {
    '1': (238, 53, 46),    # Red
    '2': (238, 53, 46),    # Red
    '3': (238, 53, 46),    # Red
    '4': (0, 147, 60),     # Green
    '5': (0, 147, 60),     # Green
    '6': (0, 147, 60),     # Green
    '7': (185, 51, 173),   # Purple
    'A': (0, 57, 166),     # Blue
    'C': (0, 57, 166),     # Blue
    'E': (0, 57, 166),     # Blue
    'B': (255, 99, 25),    # Orange
    'D': (255, 99, 25),    # Orange
    'F': (255, 99, 25),    # Orange
    'M': (255, 99, 25),    # Orange
    'G': (108, 190, 69),   # Light Green
    'J': (153, 102, 51),   # Brown
    'Z': (153, 102, 51),   # Brown
    'L': (167, 169, 172),  # Gray
    'N': (252, 204, 10),   # Yellow
    'Q': (252, 204, 10),   # Yellow
    'R': (252, 204, 10),   # Yellow
    'W': (252, 204, 10),   # Yellow
    'S': (128, 129, 131),  # Shuttle Gray
}

# SF Muni line colors
MUNI_LINE_COLORS = {
    # Metro lines
    'J':  (244, 164, 24),
    'K':  (0, 135, 82),
    'KT': (0, 135, 82),
    'L':  (204, 160, 0),
    'M':  (238, 50, 36),
    'N':  (0, 45, 130),
    'T':  (0, 135, 82),
    'S':  (100, 100, 100),
    # Bus lines (SF Muni brand red for numbered routes)
    '1':  (180, 0, 0),
    '2':  (180, 0, 0),
    '5':  (180, 0, 0),
    '6':  (180, 0, 0),
    '7':  (180, 0, 0),
    '8':  (180, 0, 0),
    '9':  (180, 0, 0),
    '10': (180, 0, 0),
    '12': (180, 0, 0),
    '14': (180, 0, 0),
    '19': (180, 0, 0),
    '21': (180, 0, 0),
    '22': (180, 0, 0),
    '24': (180, 0, 0),
    '27': (180, 0, 0),
    '28': (180, 0, 0),
    '29': (180, 0, 0),
    '30': (180, 0, 0),
    '31': (180, 0, 0),
    '33': (180, 0, 0),
    '36': (180, 0, 0),
    '37': (180, 0, 0),
    '38': (180, 0, 0),
    '43': (180, 0, 0),
    '44': (180, 0, 0),
    '45': (180, 0, 0),
    '48': (180, 0, 0),
    '49': (180, 0, 0),
    '54': (180, 0, 0),
    '55': (180, 0, 0),
    '67': (180, 0, 0),
}

# Lines that need black text (light backgrounds)
BLACK_TEXT_LINES = {'N', 'Q', 'R', 'W', 'G', 'L'}

def create_circle_sprite(line, size=19, color_map=None):
    """Create a circle sprite with centered text for a subway or Muni line."""
    if color_map is None:
        color_map = LINE_COLORS
    color = color_map.get(line, (180, 0, 0))
    text_color = (0, 0, 0) if line in BLACK_TEXT_LINES else (255, 255, 255)
    
    # Create image with transparency
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Draw filled circle
    draw.ellipse([(0, 0), (size-1, size-1)], fill=color)
    
    # Load BDF font and render text to find exact pixel bounds
    from bdfparser import Font
    font = Font("fonts/6x10.bdf")
    glyph = font.draw(line)
    bitmap = glyph.todata(2)
    
    # Find actual content bounds in the glyph
    min_row, max_row = len(bitmap), 0
    min_col, max_col = len(bitmap[0]) if bitmap else 0, 0
    
    for row_idx, row in enumerate(bitmap):
        for col_idx, pixel in enumerate(row):
            if pixel == 1:
                min_row = min(min_row, row_idx)
                max_row = max(max_row, row_idx)
                min_col = min(min_col, col_idx)
                max_col = max(max_col, col_idx)
    
    content_width = max_col - min_col + 1
    content_height = max_row - min_row + 1
    
    # Calculate position to center the content in the circle
    start_x = (size - content_width) // 2
    start_y = (size - content_height) // 2
    
    # Draw the text pixels
    for row_idx, row in enumerate(bitmap):
        for col_idx, pixel in enumerate(row):
            if pixel == 1:
                # Offset from the glyph's content start to our centered position
                px = start_x + (col_idx - min_col)
                py = start_y + (row_idx - min_row)
                if 0 <= px < size and 0 <= py < size:
                    img.putpixel((px, py), text_color + (255,))
    
    return img

def main():
    sprites_dir = "sprites"
    os.makedirs(sprites_dir, exist_ok=True)

    # NYC subway sprites
    for line in LINE_COLORS.keys():
        sprite = create_circle_sprite(line, size=19, color_map=LINE_COLORS)
        filepath = os.path.join(sprites_dir, f"circle_{line}.png")
        sprite.save(filepath)
        print(f"Created {filepath}")

    # SF Muni sprites
    for line in MUNI_LINE_COLORS.keys():
        sprite = create_circle_sprite(line, size=19, color_map=MUNI_LINE_COLORS)
        filepath = os.path.join(sprites_dir, f"circle_muni_{line}.png")
        sprite.save(filepath)
        print(f"Created {filepath}")

    total = len(LINE_COLORS) + len(MUNI_LINE_COLORS)
    print(f"\nGenerated {total} circle sprites in {sprites_dir}/")

if __name__ == "__main__":
    main()

