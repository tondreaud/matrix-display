# NYC Subway Display Mode

Display real-time NYC subway arrival times on a 64x64 RGB LED matrix, inspired by the iconic MTA station displays.

![Subway Display Demo](docs/subway_demo.png)

## Features

- 📍 Real-time MTA subway arrival data
- 🚇 Support for all NYC subway lines (1-7, A-G, J, L, M, N, Q, R, S, W, Z)
- 🎨 Official MTA line colors with pixel-perfect circle sprites
- 📺 Crisp BDF bitmap font rendering (no anti-aliasing blur)
- 🔄 Auto-refresh every 30 seconds
- 💻 Emulator support for development

## Credits & References

This implementation was inspired by and references these projects:

### [rpi-spotify-matrix-display](https://github.com/kylejohnsonkj/rpi-spotify-matrix-display)
The original project this codebase was forked from. Created by Kyle Johnson, it provides:
- Spotify album art display on 64x64 RGB LED matrices
- Raspberry Pi integration with [rpi-rgb-led-matrix](https://github.com/hzeller/rpi-rgb-led-matrix)
- Emulator support via [RGBMatrixEmulator](https://github.com/ty-porter/RGBMatrixEmulator)
- Modular app architecture that made adding the subway display mode possible

### [mta-portal](https://github.com/alejandrorascovan/mta-portal/)
CircuitPython implementation for Adafruit MatrixPortal hardware. We adopted their approach of:
- Using pre-rendered background images/sprites for pixel-perfect circle rendering
- BDF bitmap fonts (`6x10.bdf`) for crisp text without anti-aliasing
- Clean two-row layout design

### [nyct-gtfs](https://github.com/Andrew-Dickinson/nyct-gtfs)
Python library for accessing real-time NYC subway data via the MTA's GTFS-realtime feeds. Provides:
- Real-time train arrival predictions
- Trip filtering by line, direction, and stop
- Easy-to-use Python API

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure your stop in `config.ini`:
```ini
[Subway]
; Stop ID from MTA GTFS data (find yours at https://github.com/alejandrorascovan/mta-portal/#config)
stop_id = 127
; Direction: N (uptown/bronx) or S (downtown/brooklyn)
direction = N
; Comma-separated subway lines to show
lines = 1,2,3
```

3. Generate circle sprites (only needed once):
```bash
cd impl
python generate_sprites.py
```

## Usage

### Run in Emulator Mode (for development)
```bash
cd impl
python controller_v3.py -e -m subway
```

### Run on Raspberry Pi with LED Matrix
```bash
cd impl
sudo python controller_v3.py -m subway
```

## Finding Your Stop ID

Stop IDs can be found in the [MTA GTFS Static Data](http://web.mta.info/developers/data/nyct/subway/google_transit.zip) or use resources like:
- [MTA Portal Config Guide](https://github.com/alejandrorascovan/mta-portal/#config)
- [Where's The Fucking Train API](https://api.wheresthefuckingtrain.com/)

Common stop IDs:
| Station | Stop ID |
|---------|---------|
| Times Sq-42 St (1/2/3) | 127 |
| 14 St-Union Sq (4/5/6) | 635 |
| Atlantic Av-Barclays (2/3/4/5) | 235 |

## Architecture

```
impl/
├── controller_v3.py          # Main entry point with --mode subway flag
├── apps_v2/
│   └── subway_display.py     # Display rendering using sprites + BDF fonts
├── modules/
│   └── mta_module.py         # MTA data fetching via nyct-gtfs
├── sprites/                  # Pre-rendered circle sprites (generated)
│   ├── circle_1.png
│   ├── circle_2.png
│   └── ...
├── fonts/
│   └── 6x10.bdf              # Bitmap font for pixel-perfect text
└── generate_sprites.py       # Script to generate circle sprites
```

## License

MIT License - See main project LICENSE file.

