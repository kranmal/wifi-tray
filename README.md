# WiFi Tray

A small system tray app for Linux that shows every WiFi adapter in the machine and lets you switch each one on or off with a toggle switch. Built for machines with more than one adapter, for example an onboard card plus a USB dongle, where you want to pick which one stays on.

- **Left-click** the tray icon for a popup with one toggle switch per adapter, showing its name, interface and connection status.
- **Right-click** for a menu with the same toggles as checkable items, plus Quit.
- **Status icon** shows one dot under the wifi arcs per adapter: glowing green means on, hollow grey means off. With every adapter off, the arcs turn grey with a red slash.
- Asks for confirmation before turning off the last adapter that is on.
- Updates live every 3 seconds, so adapters that are plugged in or removed appear and disappear.

## How it works

Each adapter is switched through its `rfkill` soft block (`rfkill block|unblock <index>`). On systemd desktops the logged-in user already has write access to `/dev/rfkill`, so no root or polkit prompt is needed. Switching an adapter back on lets NetworkManager reconnect it by itself. Adapter names come from `udevadm`, and connection state from `nmcli`.

## Requirements

- Python 3.10+ with [PySide6](https://pypi.org/project/PySide6/) (including QtSvg)
- NetworkManager (`nmcli`)
- `rfkill` and `udevadm`
- A desktop with a StatusNotifierItem tray (KDE Plasma, and most others via an extension)

Developed and tested on KDE Plasma 6 (Wayland).

## Install

```sh
git clone https://github.com/kranmal/wifi-tray.git
cd wifi-tray
./install.sh
```

`install.sh` works per user and needs no root. It:

- writes the app icon to `~/.local/share/icons/hicolor/scalable/apps/wifi-tray.svg`
- adds an app menu entry in `~/.local/share/applications/`
- adds an autostart entry in `~/.config/autostart/`, so it starts at login

To run it without installing: `python3 wifi_tray.py`

To uninstall, delete `wifi-tray.desktop` from the two directories above and delete the icon.

## Notes

- Where the popup opens depends on the compositor. On Wayland it may not sit exactly next to the tray icon.
- Only one instance runs at a time.
- A hardware-blocked adapter (for example a laptop switch) shows a disabled toggle, because software can't override it.
- At login the app waits up to 60 seconds for the panel's tray to appear before giving up.
