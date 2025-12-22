#!/usr/bin/env python3
"""Flask webapp for configuring the Matrix Display settings."""

import os
import sys
import subprocess
import configparser
from flask import Flask, render_template, request, redirect, url_for, flash

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
        'stop_ids': config.get('Subway', 'stop_ids', fallback=config.get('Subway', 'stop_id', fallback='635')),
        'direction': config.get('Subway', 'direction', fallback='S'),
        'lines': config.get('Subway', 'lines', fallback='4,5,6'),
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
    
    flash('Settings saved successfully!', 'success')
    
    # Restart the display service only on Raspberry Pi (Linux)
    if IS_RASPBERRY_PI:
        try:
            subprocess.run(['sudo', 'systemctl', 'restart', 'matrix'], 
                          capture_output=True, timeout=10)
            flash('Display service restarted.', 'info')
        except Exception as e:
            flash(f'Could not restart service: {e}', 'warning')
    else:
        flash('Config saved. Restart the emulator manually to apply changes.', 'info')
    
    return redirect(url_for('index'))

@app.route('/restart', methods=['POST'])
def restart():
    """Restart the display service."""
    if IS_RASPBERRY_PI:
        try:
            subprocess.run(['sudo', 'systemctl', 'restart', 'matrix'], 
                          capture_output=True, timeout=10)
            flash('Display service restarted.', 'success')
        except Exception as e:
            flash(f'Could not restart service: {e}', 'error')
    else:
        flash('Not on Raspberry Pi - restart the emulator manually.', 'info')
    
    return redirect(url_for('index'))

@app.route('/display/on', methods=['POST'])
def display_on():
    """Turn the display on (start service)."""
    if IS_RASPBERRY_PI:
        try:
            subprocess.run(['sudo', 'systemctl', 'start', 'matrix'], 
                          capture_output=True, timeout=10)
            flash('Display turned ON.', 'success')
        except Exception as e:
            flash(f'Could not start display: {e}', 'error')
    else:
        flash('Not on Raspberry Pi.', 'info')
    
    return redirect(url_for('index'))

@app.route('/display/off', methods=['POST'])
def display_off():
    """Turn the display off (stop service)."""
    if IS_RASPBERRY_PI:
        try:
            subprocess.run(['sudo', 'systemctl', 'stop', 'matrix'], 
                          capture_output=True, timeout=10)
            flash('Display turned OFF.', 'success')
        except Exception as e:
            flash(f'Could not stop display: {e}', 'error')
    else:
        flash('Not on Raspberry Pi.', 'info')
    
    return redirect(url_for('index'))

if __name__ == '__main__':
    # Run on all interfaces so it's accessible on the network
    # Using port 5001 to avoid conflict with AirPlay Receiver on macOS
    app.run(host='0.0.0.0', port=80, debug=False)

