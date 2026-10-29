#!/usr/bin/env python3
"""Flask webapp for configuring the Matrix Display settings."""

import os
import sys
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
    return 'spotify'

def set_current_mode(mode):
    """Save current mode to file."""
    mode_file = os.path.join(os.path.dirname(__file__), '.current_mode')
    with open(mode_file, 'w') as f:
        f.write(mode)

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
    
    # Get current values
    settings = {
        'mode': get_current_mode(),
        'fullscreen': get_fullscreen(),
        'brightness': config.getint('Matrix', 'brightness', fallback=50),
        'stop_ids': config.get('Subway', 'stop_ids', fallback=config.get('Subway', 'stop_id', fallback='635')),
        'direction': config.get('Subway', 'direction', fallback='S'),
        'lines': config.get('Subway', 'lines', fallback='4,5,6'),
        'display_on': get_display_status(),
        'spotify_needs_reauth': get_spotify_status()[0],
        'spotify_status': get_spotify_status()[1],
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
    
    # Update Subway settings
    if 'Subway' not in config:
        config['Subway'] = {}
    config['Subway']['stop_ids'] = request.form.get('stop_ids', '635')
    config['Subway']['direction'] = request.form.get('direction', 'S')
    config['Subway']['lines'] = request.form.get('lines', '4,5,6')
    
    # Save mode and fullscreen
    mode = request.form.get('mode', 'spotify')
    set_current_mode(mode)
    
    fullscreen = request.form.get('fullscreen') == 'on'
    set_fullscreen(fullscreen)
    
    # Write config
    write_config(config)
    
    # Restart the display service only on Raspberry Pi (Linux)
    if IS_RASPBERRY_PI:
        try:
            subprocess.run(['sudo', 'systemctl', 'restart', 'matrix'], 
                          capture_output=True, timeout=10)
        except Exception:
            pass
    
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
    # Using port 5001 to avoid conflict with AirPlay Receiver on macOS
    app.run(host='0.0.0.0', port=80, debug=False)

