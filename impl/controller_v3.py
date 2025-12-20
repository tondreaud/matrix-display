import os, inspect, sys, math, time, configparser, argparse, warnings
from PIL import Image

from apps_v2 import spotify_player
from apps_v2 import subway_display
from modules import spotify_module
from modules import mta_module


def main():
    canvas_width = 64
    canvas_height = 64

    # get arguments
    parser = argparse.ArgumentParser(
                    prog = 'RpiMatrixDisplay',
                    description = 'Displays Spotify album art or NYC subway arrivals on an LED matrix')

    parser.add_argument('-f', '--fullscreen', action='store_true', help='Always display album art in fullscreen (Spotify mode)')
    parser.add_argument('-e', '--emulated', action='store_true', help='Run in a matrix emulator')
    parser.add_argument('-m', '--mode', choices=['spotify', 'subway'], default='spotify', help='Display mode: spotify or subway')
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
        print("Starting in SUBWAY mode...")
        modules = { 'mta': mta_module.MTAModule(config) }
        app = subway_display.SubwayScreen(config, modules)
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

    # generate image
    while(True):
        frame, is_active = app.generate()
        current_time = math.floor(time.time())

        if frame is not None:
            if is_active:
                last_active_time = math.floor(time.time())
            elif current_time - last_active_time >= shutdown_delay:
                frame = black_screen
        else:
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
