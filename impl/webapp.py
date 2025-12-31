#!/usr/bin/env python3
"""Flask webapp for configuring the Matrix Display settings."""

import os
import sys
import subprocess
import configparser
from flask import Flask, render_template, request, redirect, url_for

# Check if we're running on a Raspberry Pi (Linux with systemd)
IS_RASPBERRY_PI = sys.platform == 'linux'

app = Flask(__name__)
app.secret_key = 'matrix-display-secret-key'

# Path to config file (relative to impl/ directory)
CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config.ini')

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

@app.route('/')
def index():
    """Display the configuration form."""
    config = read_config()
    
    # Get current values
    settings = {
        'mode': get_current_mode(),
        'fullscreen': get_fullscreen(),
        'brightness': config.getint('Matrix', 'brightness', fallback=50),
        # Lane 1 (Top Row)
        'lane1_stop_ids': config.get('SubwayLane1', 'stop_ids', fallback='R20'),
        'lane1_direction': config.get('SubwayLane1', 'direction', fallback='N'),
        'lane1_lines': config.get('SubwayLane1', 'lines', fallback='N,Q'),
        # Lane 2 (Bottom Row)
        'lane2_stop_ids': config.get('SubwayLane2', 'stop_ids', fallback='L03'),
        'lane2_direction': config.get('SubwayLane2', 'direction', fallback='S'),
        'lane2_lines': config.get('SubwayLane2', 'lines', fallback='L'),
        'display_on': get_display_status(),
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
    
    # Update Subway Lane 1 settings (Top Row)
    if 'SubwayLane1' not in config:
        config['SubwayLane1'] = {}
    config['SubwayLane1']['stop_ids'] = request.form.get('lane1_stop_ids', 'R20')
    config['SubwayLane1']['direction'] = request.form.get('lane1_direction', 'N')
    config['SubwayLane1']['lines'] = request.form.get('lane1_lines', 'N,Q')
    
    # Update Subway Lane 2 settings (Bottom Row)
    if 'SubwayLane2' not in config:
        config['SubwayLane2'] = {}
    config['SubwayLane2']['stop_ids'] = request.form.get('lane2_stop_ids', 'L03')
    config['SubwayLane2']['direction'] = request.form.get('lane2_direction', 'S')
    config['SubwayLane2']['lines'] = request.form.get('lane2_lines', 'L')
    
    # Remove old [Subway] section if it exists
    if 'Subway' in config:
        config.remove_section('Subway')
    
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
    app.run(host='0.0.0.0', port=8080, debug=False)

