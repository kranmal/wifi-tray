#!/usr/bin/env bash
# Install WiFi Tray for the current user: app icon, app menu entry, autostart on login.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$here/wifi_tray.py"

icon_dir=~/.local/share/icons/hicolor/scalable/apps
mkdir -p "$icon_dir" ~/.local/share/applications ~/.config/autostart
python3 "$here/wifi_tray.py" --write-icon "$icon_dir/wifi-tray.svg"

entry="[Desktop Entry]
Type=Application
Name=WiFi Tray
Comment=Switch individual WiFi adapters on or off
Exec=python3 $here/wifi_tray.py
Icon=wifi-tray
Terminal=false
Categories=Network;
StartupNotify=false
X-KDE-autostart-after=panel
X-GNOME-Autostart-enabled=true
"
printf '%s' "$entry" > ~/.local/share/applications/wifi-tray.desktop
printf '%s' "$entry" > ~/.config/autostart/wifi-tray.desktop
gtk-update-icon-cache -q ~/.local/share/icons/hicolor 2>/dev/null || true
echo "Installed. Starts at next login. Run now: python3 $here/wifi_tray.py &"
