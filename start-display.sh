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

# Read fullscreen setting, default to true
FULLSCREEN_FILE=".fullscreen"
FULLSCREEN_FLAG=""
if [ -f "$FULLSCREEN_FILE" ]; then
    if [ "$(cat $FULLSCREEN_FILE)" = "true" ]; then
        FULLSCREEN_FLAG="-f"
    fi
else
    # Default to fullscreen for spotify
    FULLSCREEN_FLAG="-f"
fi

echo "Starting matrix display in $MODE mode (fullscreen: $FULLSCREEN_FLAG)..."

# Activate virtual environment and run
source /home/pi/matrix-display/.venv/bin/activate
exec python controller_v3.py -m "$MODE" $FULLSCREEN_FLAG

