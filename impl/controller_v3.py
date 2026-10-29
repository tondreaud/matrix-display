import os, inspect, sys, math, time, json, configparser, argparse, warnings
from datetime import datetime, timedelta
from PIL import Image

from apps_v2 import spotify_player
from apps_v2 import subway_display
from modules import spotify_module
from modules import mta_module


SCHEDULE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.schedule')

SUNRISE_DURATION_MINUTES = 30

def _read_schedule():
    """Read and parse the schedule file. Returns dict or None."""
    if not os.path.exists(SCHEDULE_PATH):
        return None
    try:
        with open(SCHEDULE_PATH, 'r') as f:
            return json.load(f)
    except Exception:
        return None

def is_schedule_sleeping():
    """Check if the display should be off based on the schedule file."""
    schedule = _read_schedule()
    if not schedule or not schedule.get('enabled', False):
        return False
    now = datetime.now().time()
    off_time = datetime.strptime(schedule['off_time'], '%H:%M').time()
    on_time = datetime.strptime(schedule['on_time'], '%H:%M').time()
    # Sunrise starts 30 min before on_time — not sleeping during sunrise
    sunrise_start = (datetime.combine(datetime.today(), on_time) - timedelta(minutes=SUNRISE_DURATION_MINUTES)).time()
    # If off_time > on_time, sleep window crosses midnight (e.g. 23:00 - 07:00)
    if off_time > on_time:
        sleeping = now >= off_time or now < on_time
    else:
        sleeping = off_time <= now < on_time
    if sleeping and get_sunrise_progress() > 0:
        return False  # In sunrise window, not sleeping
    return sleeping

def get_sunrise_progress():
    """Return sunrise progress 0.0-1.0 if within 30 min before wake time, else 0."""
    schedule = _read_schedule()
    if not schedule or not schedule.get('enabled', False):
        return 0.0
    now = datetime.now()
    on_time = datetime.strptime(schedule['on_time'], '%H:%M').time()
    wake_dt = datetime.combine(now.date(), on_time)
    sunrise_start_dt = wake_dt - timedelta(minutes=SUNRISE_DURATION_MINUTES)
    # Handle midnight crossing: if sunrise_start is tomorrow relative to now
    if now.time() > on_time and on_time < datetime.strptime(schedule.get('off_time', '23:00'), '%H:%M').time():
        wake_dt += timedelta(days=1)
        sunrise_start_dt = wake_dt - timedelta(minutes=SUNRISE_DURATION_MINUTES)
    if sunrise_start_dt <= now < wake_dt:
        elapsed = (now - sunrise_start_dt).total_seconds()
        total = SUNRISE_DURATION_MINUTES * 60
        return min(1.0, elapsed / total)
    return 0.0

def generate_sunrise_frame(progress, width=64, height=64):
    """Generate a warm sunrise gradient frame. Progress 0.0 (dark) to 1.0 (bright warm)."""
    img = Image.new("RGB", (width, height))
    pixels = img.load()
    # Color stops: deep red -> orange -> amber -> warm yellow
    # At low progress, very dim deep red. At full progress, bright warm light.
    for y in range(height):
        # Vertical gradient: warmer/brighter at bottom (horizon), cooler at top
        vert = 1.0 - (y / height)  # 1.0 at top, 0.0 at bottom
        horizon_boost = 1.0 - vert * 0.5  # bottom is brighter
        intensity = progress * horizon_boost
        # Blend from deep red (low progress) to bright warm yellow (high progress)
        r = int(min(255, intensity * 255))
        g = int(min(255, intensity * 200 * progress))  # green ramps up for bright yellow
        b = int(min(255, intensity * 40 * progress * progress))  # slight warmth
        for x in range(width):
            pixels[x, y] = (r, g, b)
    return img


def main():
    canvas_width = 64
    canvas_height = 64

    # get arguments
    parser = argparse.ArgumentParser(
                    prog = 'RpiMatrixDisplay',
                    description = 'Displays Spotify album art or NYC subway arrivals on an LED matrix')

    parser.add_argument('-f', '--fullscreen', action='store_true', help='Always display album art in fullscreen (Spotify mode)')
    parser.add_argument('-e', '--emulated', action='store_true', help='Run in a matrix emulator')
    parser.add_argument('-m', '--mode', choices=['spotify', 'subway', 'auto'], default='auto', help='Display mode: spotify, subway, or auto (spotify with subway fallback)')
    args = parser.parse_args()

    is_emulated = args.emulated
    is_full_screen_always = args.fullscreen
    mode = args.mode

    # switch matrix library import if emulated
    if is_emulated:
        from RGBMatrixEmulator import RGBMatrix, RGBMatrixOptions
    else:
        from rgbmatrix import RGBMatrix, RGBMatrixOptions  # type: ignore[import-not-found]

    # get config
    currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
    sys.path.append(currentdir+"/rpi-rgb-led-matrix/bindings/python")

    config = configparser.ConfigParser()
    parsed_configs = config.read('../config.ini')

    if len(parsed_configs) == 0:
        print("no config file found")
        sys.exit()

    # Initialize modules and app based on mode
    if mode == 'subway':
        print("Starting in TRANSIT mode...")
        transit_mod = mta_module.MTAModule(config)
        modules = { 'mta': transit_mod }
        app = subway_display.SubwayScreen(config, modules)
    elif mode == 'auto':
        print("Starting in AUTO mode (spotify with transit fallback)...")
        spotify_mod = spotify_module.SpotifyModule(config)
        transit_mod = mta_module.MTAModule(config)
        spotify_app = spotify_player.SpotifyScreen(config, { 'spotify': spotify_mod }, is_full_screen_always)
        subway_app = subway_display.SubwayScreen(config, { 'mta': transit_mod })
    else:
        print("Starting in SPOTIFY mode...")
        modules = { 'spotify': spotify_module.SpotifyModule(config) }
        app = spotify_player.SpotifyScreen(config, modules, is_full_screen_always)

    # setup matrix
    options = RGBMatrixOptions()
    options.hardware_mapping = config.get('Matrix', 'hardware_mapping', fallback='regular')
    options.rows = canvas_width
    options.cols = canvas_height
    options.brightness = 100 if is_emulated else config.getint('Matrix', 'brightness', fallback=100)
    options.gpio_slowdown = config.getint('Matrix', 'gpio_slowdown', fallback=1)
    options.limit_refresh_rate_hz = config.getint('Matrix', 'limit_refresh_rate_hz', fallback=0)
    options.drop_privileges = False
    matrix = RGBMatrix(options = options)

    shutdown_delay = config.getint('Matrix', 'shutdown_delay', fallback=15)
    black_screen = Image.new("RGB", (canvas_width, canvas_height), (0,0,0))
    last_active_time = math.floor(time.time())

    # Auto mode: grace period before switching to subway (avoids flicker on brief pauses)
    auto_fallback_delay = 10  # seconds of no spotify activity before showing subway
    spotify_inactive_since = None

    # generate image
    while(True):
        if mode == 'auto':
            spotify_frame, spotify_active = spotify_app.generate()

            # Track how long spotify has been inactive
            if spotify_active:
                spotify_inactive_since = None
            elif spotify_inactive_since is None:
                spotify_inactive_since = math.floor(time.time())

            # Use subway fallback if spotify has been inactive long enough
            use_subway = (spotify_inactive_since is not None and
                          math.floor(time.time()) - spotify_inactive_since >= auto_fallback_delay)

            if use_subway:
                frame, is_active = subway_app.generate()
            else:
                frame, is_active = spotify_frame, spotify_active
        else:
            frame, is_active = app.generate()

        current_time = math.floor(time.time())

        if frame is not None:
            if is_active:
                last_active_time = math.floor(time.time())
            elif current_time - last_active_time >= shutdown_delay:
                frame = black_screen
        else:
            frame = black_screen

        sunrise_progress = get_sunrise_progress()
        if sunrise_progress > 0:
            frame = generate_sunrise_frame(sunrise_progress, canvas_width, canvas_height)
        elif is_schedule_sleeping():
            frame = black_screen

        matrix.SetImage(frame)
        time.sleep(0.08)


if __name__ == '__main__':
    try:
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        main()
    except KeyboardInterrupt:
        print('Interrupted with Ctrl-C')
        sys.exit(0)
