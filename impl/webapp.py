#!/usr/bin/env python3
"""Flask webapp for configuring the Matrix Display settings."""

import os
import sys
import json
import secrets
import subprocess
import configparser
from flask import Flask, render_template, request, redirect, url_for

# Check if we're running on a Raspberry Pi (Linux with systemd)
IS_RASPBERRY_PI = sys.platform == 'linux'

app = Flask(__name__)
app.secret_key = 'matrix-display-secret-key'

# Path to config file (relative to impl/ directory)
CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config.ini')

# Spotify token cache + status, shared with the display process.
SPOTIFY_CACHE_PATH = os.path.join(os.path.dirname(__file__), '.cache')
SPOTIFY_STATUS_PATH = os.path.join(os.path.dirname(__file__), '.spotify_status')
SPOTIFY_SCOPE = "user-read-currently-playing, user-read-playback-state, user-modify-playback-state"

# Single pending authorization (hobby device, one user). Server-side because
# the Spotify callback arrives on 127.0.0.1, which cannot see cookies that
# were set on the Pi's hostname.
_pending_spotify_auth = {'state': None}


def read_config():
    """Read the current configuration."""
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH)
    return config

def write_config(config):
    """Write configuration to file."""
    with open(CONFIG_PATH, 'w') as f:
        config.write(f)

def get_current_mode():
    """Detect current mode from systemd service or default to spotify."""
    mode_file = os.path.join(os.path.dirname(__file__), '.current_mode')
    if os.path.exists(mode_file):
        with open(mode_file, 'r') as f:
            return f.read().strip()
    return 'auto'

def set_current_mode(mode):
    """Save current mode to file."""
    mode_file = os.path.join(os.path.dirname(__file__), '.current_mode')
    with open(mode_file, 'w') as f:
        f.write(mode)

PREVIOUS_MODE_PATH = os.path.join(os.path.dirname(__file__), '.previous_mode')
VALID_MODES = ('auto', 'spotify', 'subway', 'muni', 'primitive')

def get_previous_mode():
    """Mode to return to when leaving Primitive. Defaults to auto."""
    try:
        with open(PREVIOUS_MODE_PATH, 'r') as f:
            mode = f.read().strip()
        if mode in VALID_MODES and mode != 'primitive':
            return mode
    except OSError:
        pass
    return 'auto'

def set_previous_mode(mode):
    try:
        with open(PREVIOUS_MODE_PATH, 'w') as f:
            f.write(mode)
    except OSError:
        pass

def restart_display():
    """Restart the display service (Raspberry Pi only)."""
    if IS_RASPBERRY_PI:
        try:
            subprocess.run(['sudo', 'systemctl', 'restart', 'matrix'],
                           capture_output=True, timeout=20)
        except Exception:
            pass

def get_fullscreen():
    """Get fullscreen setting, default to True for Spotify."""
    fs_file = os.path.join(os.path.dirname(__file__), '.fullscreen')
    if os.path.exists(fs_file):
        with open(fs_file, 'r') as f:
            return f.read().strip() == 'true'
    return True  # Default to fullscreen

def get_display_status():
    """Check if the matrix display service is running."""
    if not IS_RASPBERRY_PI:
        return True  # Assume running in dev mode
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', 'matrix'],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() == 'active'
    except Exception:
        return False

def set_fullscreen(enabled):
    """Save fullscreen setting."""
    fs_file = os.path.join(os.path.dirname(__file__), '.fullscreen')
    with open(fs_file, 'w') as f:
        f.write('true' if enabled else 'false')

SCHEDULE_PATH = os.path.join(os.path.dirname(__file__), '.schedule')

def get_schedule():
    """Get schedule settings. Returns dict with enabled, off_time, on_time, timezone."""
    if os.path.exists(SCHEDULE_PATH):
        with open(SCHEDULE_PATH, 'r') as f:
            data = json.load(f)
            if 'timezone' not in data:
                data['timezone'] = 'America/New_York'
            return data
    return {'enabled': False, 'off_time': '23:00', 'on_time': '07:00', 'timezone': 'America/New_York'}

def set_schedule(enabled, off_time, on_time, timezone):
    """Save schedule settings and apply timezone to system clock."""
    with open(SCHEDULE_PATH, 'w') as f:
        json.dump({'enabled': enabled, 'off_time': off_time, 'on_time': on_time, 'timezone': timezone}, f)
    # Apply timezone to system clock on Raspberry Pi
    if IS_RASPBERRY_PI:
        try:
            subprocess.run(['sudo', 'timedatectl', 'set-timezone', timezone],
                          capture_output=True, timeout=5)
        except Exception:
            pass

def make_spotify_oauth(state=None):
    """Build a Spotipy OAuth manager from config.ini."""
    import spotipy
    config = read_config()
    kwargs = {
        'client_id': config.get('Spotify', 'client_id', fallback=''),
        'client_secret': config.get('Spotify', 'client_secret', fallback=''),
        'redirect_uri': config.get('Spotify', 'redirect_uri', fallback=''),
        'scope': SPOTIFY_SCOPE,
        'cache_path': SPOTIFY_CACHE_PATH,
        'open_browser': False,
    }
    if state:
        kwargs['state'] = state
    return spotipy.SpotifyOAuth(**kwargs)


def get_spotify_status():
    """Return (needs_reauth, label) from the display process status file."""
    try:
        with open(SPOTIFY_STATUS_PATH) as f:
            if f.read().strip() == 'needs_reauth':
                return True, 'Reconnect needed'
    except OSError:
        pass
    if os.path.exists(SPOTIFY_CACHE_PATH):
        return False, 'Connected'
    return True, 'Not connected'


def write_spotify_status(state):
    try:
        with open(SPOTIFY_STATUS_PATH, 'w') as f:
            f.write(state)
    except OSError:
        pass


def finish_spotify_auth(code):
    """Exchange an authorization code for tokens, verify, restart display."""
    import spotipy
    oauth = make_spotify_oauth()
    # Remove the old cache first: it may be owned by root (display service),
    # which would block this process from overwriting it in place.
    try:
        os.remove(SPOTIFY_CACHE_PATH)
    except OSError:
        pass
    token_info = oauth.get_access_token(code=code, check_cache=False)
    if not token_info:
        raise RuntimeError('Spotify did not return a token.')
    try:
        os.chmod(SPOTIFY_CACHE_PATH, 0o600)
    except OSError:
        pass
    # Verify the new token actually works before claiming success.
    sp = spotipy.Spotify(auth_manager=oauth)
    sp.current_user_playing_track()
    write_spotify_status('connected')
    if IS_RASPBERRY_PI:
        subprocess.run(['sudo', 'systemctl', 'restart', 'matrix'],
                       capture_output=True, timeout=20)


@app.route('/spotify')
def spotify_page():
    needs_reauth, _label = get_spotify_status()
    return render_template('spotify.html', spotify_needs_reauth=needs_reauth,
                           message=request.args.get('message'),
                           error=request.args.get('error'))


@app.route('/spotify/login')
def spotify_login():
    """Send the browser to Spotify. Login completes via paste or callback."""
    state = secrets.token_urlsafe(16)
    _pending_spotify_auth['state'] = state
    oauth = make_spotify_oauth(state=state)
    return redirect(oauth.get_authorize_url())


@app.route('/spotify/callback')
def spotify_callback():
    """Real callback, reached when the browser can get to this Pi directly
    (e.g. through an SSH tunnel or private HTTPS address)."""
    error = request.args.get('error')
    code = request.args.get('code')
    state = request.args.get('state')
    if error or not code:
        return ('Spotify login was not completed. You can close this tab '
                'and try again from the Matrix Display page.', 400)
    if _pending_spotify_auth['state'] and state != _pending_spotify_auth['state']:
        return ('This login link has expired. Start again from the '
                'Matrix Display page.', 400)
    try:
        finish_spotify_auth(code)
    except Exception as e:
        return (f'Spotify connection failed: {e}. You can close this tab '
                'and try again from the Matrix Display page.', 500)
    _pending_spotify_auth['state'] = None
    return ('<h1>Spotify connected</h1><p>You can close this tab. '
            'The display will update in a few seconds.</p>')


@app.route('/spotify/paste', methods=['POST'])
def spotify_paste():
    """Finish login from the pasted 127.0.0.1 redirect address."""
    pasted = (request.form.get('redirect_url') or '').strip()
    needs_reauth, _label = get_spotify_status()
    if not pasted:
        return render_template('spotify.html', spotify_needs_reauth=needs_reauth,
                               error='Paste the address from your browser first.')
    try:
        oauth = make_spotify_oauth()
        if 'error=' in pasted:
            raise RuntimeError('Spotify reported an error in that address. '
                               'Try Reconnect Spotify again.')
        code = oauth.parse_response_code(pasted)
        if not code:
            raise RuntimeError('No authorization code found in that address.')
        finish_spotify_auth(code)
    except Exception as e:
        return render_template('spotify.html', spotify_needs_reauth=True,
                               error=f'Spotify connection failed: {e}')
    _pending_spotify_auth['state'] = None
    return render_template('spotify.html', spotify_needs_reauth=False,
                           message='Spotify connected. The display will update in a few seconds.')


@app.route('/')
def index():
    """Display the configuration form."""
    config = read_config()
    current_mode = get_current_mode()
    spotify_needs_reauth, spotify_status = get_spotify_status()

    # Get current values
    settings = {
        'mode': current_mode,
        'fullscreen': get_fullscreen(),
        'brightness': config.getint('Matrix', 'brightness', fallback=50),
        'transit_provider': config.get('Matrix', 'transit_provider', fallback='subway'),
        # NYC Subway Lane 1 (Top Row)
        'lane1_stop_ids': config.get('SubwayLane1', 'stop_ids', fallback='R20'),
        'lane1_direction': config.get('SubwayLane1', 'direction', fallback='N'),
        'lane1_lines': config.get('SubwayLane1', 'lines', fallback='N,Q'),
        # NYC Subway Lane 2 (Bottom Row)
        'lane2_stop_ids': config.get('SubwayLane2', 'stop_ids', fallback='L03'),
        'lane2_direction': config.get('SubwayLane2', 'direction', fallback='S'),
        'lane2_lines': config.get('SubwayLane2', 'lines', fallback='L'),
        # SF Muni Lane 1 (Top Row)
        'muni_api_key': config.get('511', 'api_key', fallback=''),
        'muni_lane1_stop_ids': config.get('MuniLane1', 'stop_ids', fallback=''),
        'muni_lane1_direction': config.get('MuniLane1', 'direction', fallback='IB'),
        'muni_lane1_lines': config.get('MuniLane1', 'lines', fallback=''),
        'muni_lane1_destination': config.get('MuniLane1', 'destination', fallback=''),
        # SF Muni Lane 2 (Bottom Row)
        'muni_lane2_stop_ids': config.get('MuniLane2', 'stop_ids', fallback=''),
        'muni_lane2_direction': config.get('MuniLane2', 'direction', fallback='OB'),
        'muni_lane2_lines': config.get('MuniLane2', 'lines', fallback=''),
        'muni_lane2_destination': config.get('MuniLane2', 'destination', fallback=''),
        'display_on': get_display_status(),
        'primitive_active': current_mode == 'primitive',
        'spotify_needs_reauth': spotify_needs_reauth,
        'spotify_status': spotify_status,
        'schedule': get_schedule(),
    }

    return render_template('index.html', settings=settings)

@app.route('/save', methods=['POST'])
def save():
    """Save configuration and restart the display service."""
    config = read_config()
    
    # Update Matrix settings
    if 'Matrix' not in config:
        config['Matrix'] = {}
    config['Matrix']['brightness'] = request.form.get('brightness', '50')
    config['Matrix']['transit_provider'] = request.form.get('transit_provider', 'subway')

    # Update NYC Subway Lane 1 settings (Top Row)
    if 'SubwayLane1' not in config:
        config['SubwayLane1'] = {}
    config['SubwayLane1']['stop_ids'] = request.form.get('lane1_stop_ids', 'R20')
    config['SubwayLane1']['direction'] = request.form.get('lane1_direction', 'N')
    config['SubwayLane1']['lines'] = request.form.get('lane1_lines', 'N,Q')

    # Update NYC Subway Lane 2 settings (Bottom Row)
    if 'SubwayLane2' not in config:
        config['SubwayLane2'] = {}
    config['SubwayLane2']['stop_ids'] = request.form.get('lane2_stop_ids', 'L03')
    config['SubwayLane2']['direction'] = request.form.get('lane2_direction', 'S')
    config['SubwayLane2']['lines'] = request.form.get('lane2_lines', 'L')

    # Update SF Muni 511 API key
    if '511' not in config:
        config['511'] = {}
    config['511']['api_key'] = request.form.get('muni_api_key', '')

    # Update SF Muni Lane 1 settings (Top Row)
    if 'MuniLane1' not in config:
        config['MuniLane1'] = {}
    config['MuniLane1']['stop_ids'] = request.form.get('muni_lane1_stop_ids', '')
    config['MuniLane1']['direction'] = request.form.get('muni_lane1_direction', 'IB')
    config['MuniLane1']['lines'] = request.form.get('muni_lane1_lines', '')
    config['MuniLane1']['destination'] = request.form.get('muni_lane1_destination', '')

    # Update SF Muni Lane 2 settings (Bottom Row)
    if 'MuniLane2' not in config:
        config['MuniLane2'] = {}
    config['MuniLane2']['stop_ids'] = request.form.get('muni_lane2_stop_ids', '')
    config['MuniLane2']['direction'] = request.form.get('muni_lane2_direction', 'OB')
    config['MuniLane2']['lines'] = request.form.get('muni_lane2_lines', '')
    config['MuniLane2']['destination'] = request.form.get('muni_lane2_destination', '')

    # Remove legacy sections
    if 'Subway' in config:
        config.remove_section('Subway')
    if 'BARTLane1' in config:
        config.remove_section('BARTLane1')
    if 'BARTLane2' in config:
        config.remove_section('BARTLane2')
    if 'Transit' in config:
        config.remove_section('Transit')
    
    # Save mode and fullscreen
    mode = request.form.get('mode', 'spotify')
    set_current_mode(mode)
    if mode != 'primitive':
        # Keep Exit Primitive pointed at the last non-Primitive mode.
        set_previous_mode(mode)
    
    fullscreen = request.form.get('fullscreen') == 'on'
    set_fullscreen(fullscreen)

    # Save schedule
    schedule_enabled = request.form.get('schedule_enabled') == 'on'
    off_time = request.form.get('schedule_off_time', '23:00')
    on_time = request.form.get('schedule_on_time', '07:00')
    timezone = request.form.get('schedule_timezone', 'America/New_York')
    set_schedule(schedule_enabled, off_time, on_time, timezone)

    # Write config
    write_config(config)

    # Restart the display service only on Raspberry Pi (Linux)
    restart_display()

    return redirect(url_for('index'))


@app.route('/primitive/toggle', methods=['POST'])
def primitive_toggle():
    """Switch in/out of Primitive mode, remembering the previous mode."""
    if get_current_mode() == 'primitive':
        set_current_mode(get_previous_mode())
    else:
        set_previous_mode(get_current_mode())
        set_current_mode('primitive')
    restart_display()
    return redirect(url_for('index'))

@app.route('/schedule/toggle', methods=['POST'])
def schedule_toggle():
    """Toggle the sleep schedule on/off."""
    schedule = get_schedule()
    set_schedule(not schedule['enabled'], schedule['off_time'], schedule['on_time'], schedule.get('timezone', 'America/New_York'))
    return redirect(url_for('index'))

@app.route('/display/toggle', methods=['POST'])
def display_toggle():
    """Toggle the display on/off."""
    if IS_RASPBERRY_PI:
        try:
            if get_display_status():
                # Currently on, turn off
                subprocess.run(['sudo', 'systemctl', 'stop', 'matrix'], 
                              capture_output=True, timeout=10)
            else:
                # Currently off, turn on
                subprocess.run(['sudo', 'systemctl', 'start', 'matrix'], 
                              capture_output=True, timeout=10)
        except Exception:
            pass
    
    return redirect(url_for('index'))

if __name__ == '__main__':
    # Run on all interfaces so it's accessible on the network
    app.run(host='0.0.0.0', port=8080, debug=False)

