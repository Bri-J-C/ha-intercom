#!/bin/bash
# HA Intercom - Raspberry Pi Setup Script
# Run this on your Raspberry Pi

set -e

echo "================================"
echo "HA Intercom - Raspberry Pi Setup"
echo "================================"
echo

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "Please run without sudo (it will ask when needed)"
    exit 1
fi

# Install system dependencies
echo "[1/5] Installing system dependencies..."
sudo apt update
sudo apt install -y libopus-dev libportaudio2 python3-venv python3-full git

# Clone or update repo
INSTALL_DIR="$HOME/ha-intercom"
echo "[2/5] Setting up project directory..."

if [ -d "$INSTALL_DIR" ]; then
    echo "Directory exists, updating..."
    cd "$INSTALL_DIR"
    git pull || echo "Not a git repo, skipping pull"
else
    echo "Creating directory..."
    mkdir -p "$INSTALL_DIR"
    # If this script is run from the repo, copy files
    if [ -f "$(dirname "$0")/ptt_client.py" ]; then
        cp -r "$(dirname "$0")/../tools" "$INSTALL_DIR/"
    else
        echo "Please copy the 'tools' folder to $INSTALL_DIR"
    fi
fi

cd "$INSTALL_DIR"

# Create virtual environment
echo "[3/5] Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install sounddevice opuslib numpy

# Install systemd service
echo "[4/5] Installing systemd service..."
SERVICE_FILE="$INSTALL_DIR/tools/ha-intercom.service"

if [ -f "$SERVICE_FILE" ]; then
    # Update paths in service file
    sed -i "s|/home/pi|$HOME|g" "$SERVICE_FILE"
    sed -i "s|User=pi|User=$USER|g" "$SERVICE_FILE"

    sudo cp "$SERVICE_FILE" /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable ha-intercom
else
    echo "Service file not found, skipping..."
fi

# Test audio
echo "[5/5] Testing audio setup..."
echo
python3 -c "import sounddevice; print('Available audio devices:'); print(sounddevice.query_devices())" || echo "Audio test failed - check your sound card"

echo
echo "================================"
echo "Setup complete!"
echo "================================"
echo
echo "To start the intercom:"
echo "  sudo systemctl start ha-intercom"
echo
echo "To view logs:"
echo "  journalctl -u ha-intercom -f"
echo
echo "To run manually:"
echo "  cd $INSTALL_DIR"
echo "  source venv/bin/activate"
echo "  python tools/ptt_client.py --name $(hostname)"
echo
echo "Default device name: $(hostname)"
echo "Change it by editing /etc/systemd/system/ha-intercom.service"
echo
