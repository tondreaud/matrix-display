#!/bin/bash
# Wrapper script to start the matrix display with the configured mode

cd /home/pi/matrix-display/impl

# Read mode from file, default to spotify
MODE_FILE=".current_mode"
if [ -f "$MODE_FILE" ]; then
    MODE=$(cat "$MODE_FILE")
else
    MODE="spotify"
fi

echo "Starting matrix display in $MODE mode..."

# Activate virtual environment and run
source /home/pi/matrix-display/.venv/bin/activate
exec python controller_v3.py -m "$MODE"

