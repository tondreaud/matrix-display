# rpi-spotify-matrix-display

A Spotify display for 64x64 RGB LED matrices (raspberry pi project v2)

![emulator screenshot](screenshot.png)

> [!NOTE]
> You can run this project either on a raspberry pi connected to an rgb matrix or in a window that emulates a matrix display. If you don't have the components yet, emulation is a great option!

## Hardware
[Here is the list](https://www.reddit.com/r/raspberry_pi/comments/ombwwg/my_64x64_rgb_led_matrix_album_art_display_pi_3b/) of hardware I used. You can ignore the software details as they are irrelevant for v2.

## Spotify Pre-Setup
1. Go to https://developer.spotify.com/dashboard
2. Create an account and/or login
3. Select "Create app" (name/description does not matter)
4. Add http://127.0.0.1:8080/callback under Redirect URIs
5. Save, then tap "Settings" in the upper right
6. Copy the generated Client ID and Secret ID for later

## WiFi Setup 

This device uses **Comitup** for easy WiFi configuration - no technical knowledge required!

### First-Time Setup
1. **Power on** the Raspberry Pi
2. **Wait ~1 minute** for it to boot
3. If it can't find a known WiFi network, it will create a hotspot called **"MatrixDisplay-Setup"**
4. **Connect your phone/laptop** to the "MatrixDisplay-Setup" WiFi network
5. A **captive portal** should automatically open (if not, navigate to http://10.41.0.1/)
6. **Select your home WiFi** from the list and enter the password
7. The Pi will connect to your WiFi and the display will start automatically!

### Reconnecting to a Different WiFi
If you move the device to a new location or change your WiFi:
1. The Pi will automatically create the "MatrixDisplay-Setup" hotspot when it can't connect
2. Follow the steps above to configure the new network

## Web Interface

The Matrix Display includes a web-based settings interface for easy configuration.

- **URL:** `http://<pi-ip-address>:8080`
- **Features:**
  - Switch between Spotify and NYC Subway display modes
  - Adjust brightness
  - Configure subway stations and lines (two independent lanes)
  - Turn display on/off

---

## Pi Setup (For Developers)

> [!IMPORTANT]
> Please see the [pi setup wiki page](https://github.com/kylejohnsonkj/rpi-spotify-matrix-display/wiki/raspberry-pi-full-setup-guide) for a full installation guide!

https://github.com/user-attachments/assets/9bf163f9-8e0f-47cc-b2d2-a62b3a975471

<sup>The above video is from [my reddit post here.](https://www.reddit.com/r/raspberry_pi/comments/ziz4hk/my_64x64_rgb_led_matrix_album_art_display_pi_3b/)</sup>

## Emulator Setup

1. Clone and enter the repo
   - `git clone --recurse-submodules https://github.com/kylejohnsonkj/rpi-spotify-matrix-display`
   - `cd rpi-spotify-matrix-display/`
2. **Set your Client ID and Secret ID in the config.ini** 🙂
3. Create and activate a python [virtual environment](https://packaging.python.org/en/latest/guides/installing-using-pip-and-virtual-environments/)
   - `python3 -m venv .venv`
   - `source .venv/bin/activate`
4. Install dependencies
   - `python3 -m pip install -r requirements.txt`
5. Run the controller emulated (-e) from the impl/ directory
   - `cd impl/`
   - `python3 controller_v3.py -e`
6. Authorize Spotify
   - After running, follow instructions provided in the console. Pasted link should begin with http://127.0.0.1:8080/callback
   - After successful authorization, play a song and the display will appear!

## Arguments
| Argument | Default | Description |
| :- | :- | :- |
|`-e` , `--emulated`| false | Run in a matrix emulator |
|`-f` , `--fullscreen`| false | Always display album art in full screen (64x64) |
|`-h` , `--help`| false | Display help messages for arguments |

## Configuration
Configuration is handled in the config.ini. I have included my own as a sample.

For Matrix configuration, see https://github.com/hzeller/rpi-rgb-led-matrix#changing-parameters-via-command-line-flags. More extensive customization can be done in `impl/controller_v3.py` directly.

For Spotify configuration, set the `client_id` and `client_secret` to your own. You may leave `redirect_uri` alone. I have also included a `device_whitelist` which is disabled by default.

## Acknowledgements
Thanks to allenslab for providing the original codebase for this project, [matrix-dashboard](https://github.com/allenslab/matrix-dashboard). You can find his original reddit post [here](https://www.reddit.com/r/3Dprinting/comments/ujyy4g/i_designed_and_3d_printed_a_led_matrix_dashboard/). This project is an adaption of his Spotify app for 64x64 matrices, while also packing some other improvements.

Thanks to ty-porter for [his fork](https://github.com/ty-porter/matrix-dashboard) of matrix-dashboard from which my development branched from. The emulation support his [RGBMatrixEmulator project](https://github.com/ty-porter/RGBMatrixEmulator) added made it a breeze to develop efficiently.

And finally, thanks to hzeller for his work on [rpi-rgb-led-matrix](https://github.com/hzeller/rpi-rgb-led-matrix).
