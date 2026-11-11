#!/bin/bash
# frAInk - device setup for a fresh Raspberry Pi OS Lite (64-bit, Trixie)
# on a Pi Zero 2 W + PiSugar 3 + PiSugar WhisPlay.
#
#   git clone https://github.com/<you>/fraink-ai ~/fraink-ai && cd ~/fraink-ai && ./setup.sh
#
# Safe to re-run. Reboot when it finishes.
set -e
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
export DEBIAN_FRONTEND=noninteractive

echo "=== 1/7 system packages ==="
sudo apt-get update
sudo apt-get install -y git libopus0 libportaudio2 python3-venv python3-pip \
    python3-spidev python3-lgpio python3-gpiozero python3-numpy i2c-tools iw alsa-utils

echo "=== 2/7 I2C + SPI ==="
sudo raspi-config nonint do_i2c 0
sudo raspi-config nonint do_spi 0

echo "=== 3/7 WhisPlay display + WM8960 audio driver ==="
if [ ! -d "$HOME/Whisplay" ]; then
    git clone https://github.com/PiSugar/Whisplay.git "$HOME/Whisplay"
fi
(cd "$HOME/Whisplay" && echo y | sudo bash script/install_raspberry_pi.sh)

echo "=== 4/7 PiSugar 3 power manager ==="
if ! dpkg -s pisugar-server >/dev/null 2>&1; then
    wget -q -O /tmp/pisugar-power-manager.sh https://cdn.pisugar.com/release/pisugar-power-manager.sh
    sudo bash /tmp/pisugar-power-manager.sh -c release --non-interactive --model "PiSugar 3"
fi
# PiSugar button taps -> trigger files the app watches; safe shutdown at 5 %
sudo python3 - <<'EOF'
import json, os
p = "/etc/pisugar-server/config.json"
cfg = json.load(open(p)) if os.path.exists(p) else {}
cfg.update({
    "single_tap_enable": True, "single_tap_shell": "touch /tmp/pisugar-companion/single",
    "double_tap_enable": False, "double_tap_shell": "touch /tmp/pisugar-companion/double",
    "long_tap_enable": True, "long_tap_shell": "touch /tmp/pisugar-companion/long",
    "auto_shutdown_level": 5.0, "auto_shutdown_delay": 30.0,
})
os.makedirs(os.path.dirname(p), exist_ok=True)
json.dump(cfg, open(p, "w"), indent=2)
EOF
sudo systemctl restart pisugar-server || true

echo "=== 5/7 Wi-Fi: power saving off (prevents dropouts) + captive-portal DNS ==="
printf "[connection]\nwifi.powersave = 2\n" | sudo tee /etc/NetworkManager/conf.d/99-wifi-powersave-off.conf >/dev/null
sudo mkdir -p /etc/NetworkManager/dnsmasq-shared.d
echo "address=/#/192.168.4.1" | sudo tee /etc/NetworkManager/dnsmasq-shared.d/pisugar-portal.conf >/dev/null
sudo nmcli general reload conf || true

echo "=== 6/7 Tailscale ==="
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh

echo "=== 7/7 Python venv + app service ==="
cd "$APP_DIR"
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
mkdir -p notes/audio meetings data
[ -f config.yaml ] || cp config.example.yaml config.yaml

sed -e "s|/home/pi/fraink-ai|$APP_DIR|g" -e "s|^User=pi|User=$USER|" fraink.service \
    | sudo tee /etc/systemd/system/fraink.service >/dev/null
sudo mkdir -p /etc/systemd/system/fraink.service.d
printf "[Service]\n# Wi-Fi setup captive portal listens on :80\nAmbientCapabilities=CAP_NET_BIND_SERVICE\n" \
    | sudo tee /etc/systemd/system/fraink.service.d/portal-port80.conf >/dev/null
sudo usermod -aG audio,video,gpio,input,spi,i2c "$USER"
sudo systemctl daemon-reload
sudo systemctl enable fraink.service

cat <<EOF

=== Done ===
1. Edit $APP_DIR/config.yaml (server URLs, ntfy token, email app password).
2. sudo tailscale up --hostname=fraink    (open the login link it prints)
3. sudo reboot
Logs: journalctl -u fraink -f
EOF
