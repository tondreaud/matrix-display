#!/usr/bin/env python3
"""Flask webapp for configuring the Matrix Display settings."""

import os
import subprocess
import configparser
from flask import Flask, render_template, request, redirect, url_for, flash

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
    # Check if there's a mode file, otherwise default to spotify
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

@app.route('/')
def index():
    """Display the configuration form."""
    config = read_config()
    
    # Get current values
    settings = {
        'mode': get_current_mode(),
        'brightness': config.getint('Matrix', 'brightness', fallback=50),
        'stop_id': config.get('Subway', 'stop_id', fallback='635'),
        'direction': config.get('Subway', 'direction', fallback='S'),
        'lines': config.get('Subway', 'lines', fallback='4,5,6'),
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
    config['Subway']['stop_id'] = request.form.get('stop_id', '635')
    config['Subway']['direction'] = request.form.get('direction', 'S')
    config['Subway']['lines'] = request.form.get('lines', '4,5,6')
    
    # Save mode
    mode = request.form.get('mode', 'spotify')
    set_current_mode(mode)
    
    # Write config
    write_config(config)
    
    flash('Settings saved successfully!', 'success')
    
    # Restart the display service if running on Pi
    try:
        subprocess.run(['sudo', 'systemctl', 'restart', 'matrix-display'], 
                      capture_output=True, timeout=10)
        flash('Display service restarted.', 'info')
    except Exception as e:
        flash(f'Note: Could not restart service (may not be on Pi): {e}', 'warning')
    
    return redirect(url_for('index'))

@app.route('/restart', methods=['POST'])
def restart():
    """Restart the display service."""
    try:
        subprocess.run(['sudo', 'systemctl', 'restart', 'matrix-display'], 
                      capture_output=True, timeout=10)
        flash('Display service restarted.', 'success')
    except Exception as e:
        flash(f'Could not restart service: {e}', 'error')
    
    return redirect(url_for('index'))

if __name__ == '__main__':
    # Run on all interfaces so it's accessible on the network
    app.run(host='0.0.0.0', port=5000, debug=False)

