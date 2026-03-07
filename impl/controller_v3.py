import os, inspect, sys, math, time, json, configparser, argparse, warnings
from datetime import datetime
from PIL import Image

from apps_v2 import spotify_player
from apps_v2 import subway_display
from modules import spotify_module
from modules import mta_module
from modules import bart_module


SCHEDULE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.schedule')

def is_schedule_sleeping():
    """Check if the display should be off based on the schedule file."""
    if not os.path.exists(SCHEDULE_PATH):
        return False
    try:
        with open(SCHEDULE_PATH, 'r') as f:
            schedule = json.load(f)
        if not schedule.get('enabled', False):
            return False
        now = datetime.now().time()
        off_time = datetime.strptime(schedule['off_time'], '%H:%M').time()
        on_time = datetime.strptime(schedule['on_time'], '%H:%M').time()
        # If off_time > on_time, sleep window crosses midnight (e.g. 23:00 - 07:00)
        if off_time > on_time:
            return now >= off_time or now < on_time
        else:
            # Same-day window (e.g. 01:00 - 06:00)
            return off_time <= now < on_time
    except Exception:
        return False


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

    # Determine transit city (nyc or sf)
    transit_city = config.get('Transit', 'city', fallback='nyc').lower()

    def create_transit_module():
        if transit_city == 'sf':
            print(f"Using BART transit module (San Francisco)")
            return bart_module.BARTModule(config)
        else:
            print(f"Using MTA transit module (New York City)")
            return mta_module.MTAModule(config)

    # Initialize modules and app based on mode
    if mode == 'subway':
        print("Starting in TRANSIT mode...")
        transit_mod = create_transit_module()
        modules = { 'mta': transit_mod }
        app = subway_display.SubwayScreen(config, modules)
    elif mode == 'auto':
        print("Starting in AUTO mode (spotify with transit fallback)...")
        spotify_mod = spotify_module.SpotifyModule(config)
        transit_mod = create_transit_module()
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

        if is_schedule_sleeping():
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
