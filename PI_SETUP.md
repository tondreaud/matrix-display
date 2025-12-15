# Raspberry Pi Setup Guide

Complete setup guide for the Matrix Display on a Raspberry Pi.

## Prerequisites

- Raspberry Pi (3B+ or newer recommended)
- 64x64 RGB LED Matrix with Adafruit HAT
- MicroSD card (16GB+) with Raspberry Pi OS

## Step 1: Flash Raspberry Pi OS

1. Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Flash **Raspberry Pi OS Lite (64-bit)** to SD card
3. In Imager settings (gear icon), enable SSH and set username/password

## Step 2: Initial Pi Setup

Boot the Pi and SSH in (or connect keyboard/monitor):

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install required packages
sudo apt install -y git python3-pip python3-venv network-manager
```

## Step 3: Install Balena WiFi Connect

This creates a setup hotspot when the Pi isn't connected to WiFi - perfect for giving the device to someone else.

```bash
# Install wifi-connect
bash <(curl -sL https://github.com/balena-os/wifi-connect/raw/master/scripts/raspbian-install.sh)
```

Create the startup script:

```bash
sudo nano /usr/local/bin/wifi-connect-start.sh
```

Paste this content:

```bash
#!/bin/bash
sleep 30
if ! iwgetid -r; then
    wifi-connect --portal-ssid "MatrixDisplay-Setup"
fi
```

Make it executable and create a service:

```bash
sudo chmod +x /usr/local/bin/wifi-connect-start.sh

sudo tee /etc/systemd/system/wifi-connect.service << EOF
[Unit]
Description=WiFi Connect
After=NetworkManager.service

[Service]
Type=simple
ExecStart=/usr/local/bin/wifi-connect-start.sh
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable wifi-connect
```

**How it works for the recipient:**
1. Plug in the Pi (no monitor needed)
2. Wait ~1 minute for "MatrixDisplay-Setup" WiFi to appear
3. Connect phone to that network
4. A captive portal opens - enter home WiFi credentials
5. Pi connects and the setup hotspot disappears

## Step 4: Clone and Install the Project

```bash
cd ~
git clone https://github.com/tondreaud/matrix-display.git
cd matrix-display

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Make start script executable
chmod +x start-display.sh
```

## Step 5: Install Systemd Services

```bash
# Copy service files
sudo cp matrix-display.service /etc/systemd/system/
sudo cp matrix-webapp.service /etc/systemd/system/

# Enable and start services
sudo systemctl daemon-reload
sudo systemctl enable matrix-display
sudo systemctl enable matrix-webapp
sudo systemctl start matrix-display
sudo systemctl start matrix-webapp
```

## Step 6: Access the Web Interface

Once on the same WiFi network, access the configuration page at:

```
http://raspberrypi.local:5000
```

Or set a custom hostname:

```bash
sudo hostnamectl set-hostname matrix
# Then access: http://matrix.local:5000
```

## Service Commands

```bash
# Check status
sudo systemctl status matrix-display
sudo systemctl status matrix-webapp

# View logs
journalctl -u matrix-display -f
journalctl -u matrix-webapp -f

# Restart services
sudo systemctl restart matrix-display
sudo systemctl restart matrix-webapp
```

## Troubleshooting

### Can't find the Pi on the network?

```bash
# On the Pi, check the IP address
hostname -I

# Or scan your network from another computer
# macOS: dns-sd -B _http._tcp
# Linux: avahi-browse -a
```

### Display not working?

```bash
# Check if the service is running
sudo systemctl status matrix-display

# Run manually to see errors
cd ~/matrix-display/impl
source ../.venv/bin/activate
sudo python controller_v3.py -m subway
```

### WiFi Connect not working?

```bash
# Check NetworkManager is running
sudo systemctl status NetworkManager

# Check wifi-connect service
sudo systemctl status wifi-connect
journalctl -u wifi-connect -f
```

