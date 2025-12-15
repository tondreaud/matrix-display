import time, threading, os
from PIL import Image, ImageDraw
from bdfparser import Font

class SubwayScreen:
    def __init__(self, config, modules):
        self.modules = modules
        self.mta_module = modules.get('mta')
        
        # Load BDF bitmap font for pixel-perfect rendering (like mta-portal project)
        self.font = Font("fonts/6x10.bdf")
        
        # Load pre-rendered circle sprites (like mta-portal uses background images)
        self.circle_sprites = {}
        self._load_sprites()
        
        # Canvas dimensions
        self.canvas_width = 64
        self.canvas_height = 64
        
        # Colors matching reference images
        self.dest_color = (100, 180, 255)       # Light blue/cyan for destination
        self.time_color = (255, 200, 50)        # Yellow/gold for times
        self.bg_color = (0, 0, 0)
        self.separator_color = (60, 60, 120)    # Bluish dots for separator
        
        # Layout constants for two-row display
        self.row1_y = 0        # First train row Y position
        self.row2_y = 33       # Second train row Y position
        
        self.circle_x = 1      # X position for circle sprite
        self.text_x = 22       # X position for destination/times text
        
        # Current arrivals data
        self.current_arrivals = []
        
        # Scrolling state for destination text (continuous scrolling)
        self.scroll_offset = 0
        self.scroll_speed = 0.5  # Pixels per frame
        self.text_area_width = self.canvas_width - self.text_x  # Available width for text
        
        # Data fetching thread
        self.arrivals_data = []
        self.thread = threading.Thread(target=self._fetch_arrivals_async, daemon=True)
        self.thread.start()
    
    def _load_sprites(self):
        """Load pre-rendered circle sprites for each subway line"""
        sprites_dir = "sprites"
        lines = ['1', '2', '3', '4', '5', '6', '7', 
                 'A', 'B', 'C', 'D', 'E', 'F', 'G', 
                 'J', 'L', 'M', 'N', 'Q', 'R', 'S', 'W', 'Z']
        
        for line in lines:
            sprite_path = os.path.join(sprites_dir, f"circle_{line}.png")
            if os.path.exists(sprite_path):
                # Load and convert to RGB (remove alpha by compositing on black)
                sprite = Image.open(sprite_path).convert('RGBA')
                # Create black background and composite
                bg = Image.new('RGB', sprite.size, (0, 0, 0))
                bg.paste(sprite, mask=sprite.split()[3])  # Use alpha as mask
                self.circle_sprites[line] = bg
                print(f"[Subway Display] Loaded sprite for line {line}")
        
        print(f"[Subway Display] Loaded {len(self.circle_sprites)} circle sprites")
    
    def _fetch_arrivals_async(self):
        """Background thread to fetch arrival data"""
        time.sleep(2)  # Initial delay
        while True:
            if self.mta_module:
                self.arrivals_data = self.mta_module.getArrivals()
            time.sleep(5)  # Fetch every 5 seconds
    
    def generate(self):
        """Generate a frame for the LED matrix"""
        # Get latest arrivals from module queue or cached data
        if self.mta_module and not self.mta_module.queue.empty():
            self.current_arrivals = self.mta_module.queue.get()
            self.mta_module.queue.queue.clear()
        elif self.arrivals_data:
            self.current_arrivals = self.arrivals_data
        
        return self._generate_frame(self.current_arrivals)
    
    def _generate_frame(self, arrivals):
        """Render the subway display frame"""
        frame = Image.new("RGB", (self.canvas_width, self.canvas_height), self.bg_color)
        draw = ImageDraw.Draw(frame)
        
        if not arrivals:
            # No arrivals - show waiting message
            self._draw_bdf_text(frame, 4, 12, "Waiting", self.dest_color)
            self._draw_bdf_text(frame, 4, 24, "for data", self.dest_color)
            return (frame, False)
        
        # Update scroll offset for long text
        self._update_scroll(arrivals)
        
        # Draw first line (row 1)
        if len(arrivals) >= 1:
            self._draw_line_row(draw, frame, arrivals[0], self.row1_y, 0)
        
        # Draw dotted separator line between rows
        self._draw_dotted_line(draw, 32)
        
        # Draw second line (row 2)
        if len(arrivals) >= 2:
            self._draw_line_row(draw, frame, arrivals[1], self.row2_y, 1)
        
        return (frame, True)
    
    def _update_scroll(self, arrivals):
        """Update scroll offset for continuous looping marquee animation"""
        # Find max text width that needs scrolling
        max_text_width = 0
        for arrival in arrivals:
            text_width = self._get_text_width(arrival['direction'])
            if text_width > self.text_area_width and text_width > max_text_width:
                max_text_width = text_width
        
        if max_text_width <= self.text_area_width:
            # No scrolling needed
            self.scroll_offset = 0
            return
        
        # Continuous scroll
        self.scroll_offset += self.scroll_speed
        
        # Reset for seamless loop (when first copy has scrolled text_width + gap)
        gap = 20
        total_scroll_width = max_text_width + gap
        if self.scroll_offset >= total_scroll_width:
            self.scroll_offset = 0  # Seamless reset
    
    def _draw_bdf_text(self, image, x, y, text, color, clip_left=None, clip_right=None):
        """Draw text using BDF bitmap font - pixel perfect rendering"""
        glyph = self.font.draw(text, missing="?")
        bitmap = glyph.todata(2)  # Get as 2D array
        
        # Default clip boundaries
        left_bound = clip_left if clip_left is not None else 0
        right_bound = clip_right if clip_right is not None else self.canvas_width
        
        for row_idx, row in enumerate(bitmap):
            for col_idx, pixel in enumerate(row):
                if pixel == 1:  # Foreground pixel
                    px = x + col_idx
                    py = y + row_idx
                    # Clip to boundaries
                    if left_bound <= px < right_bound and 0 <= py < self.canvas_height:
                        image.putpixel((px, py), color)
    
    def _get_text_width(self, text):
        """Get the pixel width of text"""
        glyph = self.font.draw(text, missing="?")
        return glyph.width()
    
    def _draw_dotted_line(self, draw, y):
        """Draw a dotted horizontal separator line"""
        for x in range(0, self.canvas_width, 2):
            draw.point((x, y), fill=self.separator_color)
    
    def _draw_line_row(self, draw, frame, line_data, y_pos, row_index=0):
        """Draw a single line's arrival info"""
        line = line_data['line']
        direction = line_data['direction']
        times = line_data['times']
        
        # Paste pre-rendered circle sprite (like mta-portal's background image approach)
        if line in self.circle_sprites:
            sprite = self.circle_sprites[line]
            # Center vertically in row (sprite is 19x19, row is ~30px)
            sprite_y = y_pos + 6
            frame.paste(sprite, (self.circle_x, sprite_y))
        
        # Draw direction text in cyan/blue (first line of text) - with scrolling if needed
        dest_y = y_pos + 4
        text_width = self._get_text_width(direction)
        
        if text_width > self.text_area_width:
            # Text is too long - apply looping marquee scroll
            scroll_x = self.text_x - int(self.scroll_offset)
            gap = 20  # Gap between end of text and start of repeated text
            total_scroll_width = text_width + gap
            
            # Draw first copy of text
            self._draw_bdf_text(frame, scroll_x, dest_y, direction, self.dest_color, 
                               clip_left=self.text_x, clip_right=self.canvas_width)
            
            # Draw second copy (looping) that comes in from the right
            scroll_x2 = scroll_x + total_scroll_width
            self._draw_bdf_text(frame, scroll_x2, dest_y, direction, self.dest_color,
                               clip_left=self.text_x, clip_right=self.canvas_width)
        else:
            # Text fits - no scrolling needed
            self._draw_bdf_text(frame, self.text_x, dest_y, direction, self.dest_color)
        
        # Draw times in yellow with subscript dot separators (like reference image)
        times_y = y_pos + 16
        self._draw_times_with_dots(frame, self.text_x, times_y, times, self.time_color)
    
    def _draw_times_with_dots(self, image, x, y, times, color):
        """Draw arrival times with small subscript dots as separators (like reference image)"""
        current_x = x
        
        for i, t in enumerate(times):
            # Draw the number
            time_str = str(t['minutes'])
            self._draw_bdf_text(image, current_x, y, time_str, color)
            
            # Get width of the number we just drew
            glyph = self.font.draw(time_str)
            current_x += glyph.width()
            
            # Draw subscript dot separator (except after last number)
            if i < len(times) - 1:
                # Small dot positioned at baseline (lower than text)
                dot_x = current_x + 1
                dot_y = y + 7  # Near bottom of text
                image.putpixel((dot_x, dot_y), color)
                current_x += 4  # Space after dot
