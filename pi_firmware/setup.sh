#!/bin/bash
echo "Starting Setup"
set -e

# One-time OS provisioning. Pairing happens at runtime in main.py.
SENTINEL="/etc/rem-provisioned"
if [ -f "$SENTINEL" ]; then
  echo "Already provisioned (found $SENTINEL) -- setup.sh only runs once. Nothing to do."
  exit 0
fi

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/venv"

echo "Configuring Python virtual environment"

# The Pi has no RTC; apt signature checks fail until NTP sets the clock.
echo "Waiting for system clock to sync via NTP"
for i in $(seq 1 30); do
  if [ "$(timedatectl show --property=NTPSynchronized --value 2>/dev/null)" = "yes" ]; then
    echo "Clock synced"
    break
  fi
  sleep 2
done

# May run from cloud-init with no terminal; suppress debconf and
# needrestart prompts that would otherwise hang apt.
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a

sudo apt update
# xserver-xorg + xinit: bare X for the Chromium kiosk on Pi OS Lite.
# x11-xserver-utils: xrandr/xset used by kiosk.py.
# plymouth + rpd-plym-splash: boot splash ("pix" theme). mpv: boot video.
sudo apt install -y python3 python3-venv avahi-daemon xserver-xorg xinit x11-xserver-utils \
  chromium plymouth rpd-plym-splash mpv

echo "Configuring Pi - Installing the boot intro video"
sudo mkdir -p /opt/rem-boot-video
sudo cp "$PROJECT_DIR/boot_video/rem-logo.mp4" /opt/rem-boot-video/rem-logo.mp4

sudo tee /etc/systemd/system/boot-video.service > /dev/null << 'EOF'
[Unit]
Description=REM boot intro video
After=systemd-udev-settle.service plymouth-start.service
Before=relay-controller.service

[Service]
Type=oneshot
# Release the display from Plymouth so mpv can take DRM master.
ExecStartPre=-plymouth quit
ExecStart=mpv --fs --no-audio --really-quiet --hwdec=auto --vo=drm /opt/rem-boot-video/rem-logo.mp4

[Install]
WantedBy=multi-user.target
EOF

# Hides kernel boot text behind the Plymouth splash.
sudo raspi-config nonint do_boot_splash 0

python3 -m venv "$VENV_DIR" --system-site-packages

echo "Installing GPIO library"
# rpi-lgpio provides the RPi.GPIO API and works on the Pi 5. Installed in the
# venv so it shadows any system RPi.GPIO.
"$VENV_DIR/bin/pip" install rpi-lgpio
"$VENV_DIR/bin/python" -c "import RPi.GPIO"

echo "Installing websockets library"
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

echo "Configuring Pi - Broadcasting this Pi over mDNS so the admin panel can find it"
sudo mkdir -p /etc/avahi/services
sudo tee /etc/avahi/services/rem-field-node.service > /dev/null << 'EOF'
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">%h</name>
  <service>
    <type>_rem-fieldnode._tcp</type>
    <!-- claim_listener.py CLAIM_PORT. Avoid double hyphens in this comment: Avahi rejects the file. -->
    <port>8765</port>
  </service>
</service-group>
EOF
sudo systemctl restart avahi-daemon

echo "Configuring Pi - Adding to boot service"

sudo tee /etc/systemd/system/relay-controller.service > /dev/null << EOF
[Unit]
Description=REM Field Node / Kiosk
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
# Runs as root so kiosk.py can start X without a display manager.
WorkingDirectory=$PROJECT_DIR
ExecStart=$VENV_DIR/bin/python $PROJECT_DIR/main.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

echo "Configuring Pi - Reloading Pi startup config"

sudo systemctl daemon-reload

echo "Configuring Pi - Enabling firmware run on startup"

sudo systemctl enable relay-controller.service
sudo systemctl enable boot-video.service

sudo touch "$SENTINEL"

if [ -t 0 ]; then
  echo "Rebooting Pi"
  sudo reboot
else
  # Non-interactive (first-boot script): the caller handles the reboot.
  echo "Running non-interactively -- setup is done, leaving the reboot to the caller."
fi
