#!/usr/bin/env python3
"""WiFi Tray: system tray app to switch individual WiFi adapters on/off.

Each adapter is toggled through its rfkill switch (soft block), which the logged-in
user may change via the /dev/rfkill ACL. No root needed. NetworkManager reconnects
automatically when an adapter is switched back on.

Left-click tray icon: popup with a toggle switch per adapter.
Right-click: menu with the same toggles as checkable items.
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import time
from dataclasses import dataclass

from PySide6.QtCore import QLockFile, QPoint, QRectF, QSize, Qt, QTimer, QStandardPaths
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

REFRESH_MS = 3000


# --------------------------------------------------------------------------- model
@dataclass
class Adapter:
    ifname: str
    label: str
    bus: str
    driver: str
    rfkill_idx: int | None
    soft_blocked: bool
    hard_blocked: bool
    nm_state: str  # e.g. "connected", "disconnected", "unavailable"
    connection: str  # connection profile name, "" if none

    @property
    def enabled(self) -> bool:
        return not self.soft_blocked and not self.hard_blocked

    @property
    def status_text(self) -> str:
        if self.hard_blocked:
            return "Blocked by hardware switch"
        if self.soft_blocked:
            return "Off"
        if self.nm_state == "connected" and self.connection:
            return f"Connected to {self.connection}"
        if self.nm_state.startswith("connect"):
            return "Connecting…"
        return "On, not connected"


def _read(path: str, default: str = "") -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def _udev_props(ifname: str) -> dict[str, str]:
    try:
        out = subprocess.run(
            ["udevadm", "info", "-q", "property", "-p", f"/sys/class/net/{ifname}"],
            capture_output=True, text=True, timeout=3,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    props = {}
    for line in out.splitlines():
        k, _, v = line.partition("=")
        props[k] = v
    return props


def _nm_states() -> dict[str, tuple[str, str]]:
    """ifname -> (state, connection name)."""
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"],
            capture_output=True, text=True, timeout=3,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    states = {}
    for line in out.splitlines():
        # nmcli -t escapes ':' inside values as '\:'
        parts = line.replace("\\:", "\x00").split(":")
        if len(parts) >= 4 and parts[1] == "wifi":
            conn = parts[3].replace("\x00", ":")
            states[parts[0]] = (parts[2], "" if conn == "--" else conn)
    return states


_label_cache: dict[str, tuple[str, str, str]] = {}


def _describe(ifname: str) -> tuple[str, str, str]:
    """Return (human label, bus, driver). Cached: hardware identity doesn't change."""
    if ifname in _label_cache:
        return _label_cache[ifname]
    p = _udev_props(ifname)
    bus = p.get("ID_BUS", "")
    driver = p.get("ID_NET_DRIVER", "")
    model = p.get("ID_MODEL_FROM_DATABASE") or p.get("ID_MODEL", "").replace("_", " ")
    # Trim long PCI database strings: "Wi-Fi 5(...) [Thunder Peak] (Dual Band ... 9260 ...)"
    if "(" in model and p.get("ID_VENDOR_FROM_DATABASE"):
        paren = model[model.rfind("(") + 1 : model.rfind(")")]
        short = model.split("(")[0].strip()
        toks = paren.split()
        for i, tok in enumerate(toks):
            if tok.startswith("Wireless-AC"):
                short = f"{short} {' '.join(toks[i:i + 2])}".strip()
                break
        model = short
    vendor = p.get("ID_VENDOR_FROM_DATABASE", "")
    label = f"{vendor} {model}".strip() if vendor else (model or ifname)
    if bus:
        label = f"{label} ({bus.upper()})"
    _label_cache[ifname] = (label, bus, driver)
    return _label_cache[ifname]


def discover() -> list[Adapter]:
    nm = _nm_states()
    adapters = []
    for path in sorted(glob.glob("/sys/class/net/*/phy80211")):
        ifname = path.split("/")[4]
        rk = glob.glob(f"{path}/rfkill*")
        idx = soft = hard = None
        if rk:
            idx = int(_read(f"{rk[0]}/index", "-1"))
            soft = _read(f"{rk[0]}/soft") == "1"
            hard = _read(f"{rk[0]}/hard") == "1"
        label, bus, driver = _describe(ifname)
        state, conn = nm.get(ifname, ("unknown", ""))
        adapters.append(
            Adapter(ifname, label, bus, driver, idx, bool(soft), bool(hard), state, conn)
        )
    return adapters


def set_enabled(adapter: Adapter, on: bool) -> str | None:
    """Switch adapter on/off. Returns an error string or None."""
    if adapter.rfkill_idx is None:
        return f"{adapter.ifname} has no rfkill switch."
    try:
        r = subprocess.run(
            ["rfkill", "unblock" if on else "block", str(adapter.rfkill_idx)],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return str(e)
    return None if r.returncode == 0 else (r.stderr.strip() or "rfkill failed")


# ---------------------------------------------------------------------------- UI
class ToggleSwitch(QAbstractButton):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(QSize(46, 26))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        pal = self.palette()
        if not self.isEnabled():
            track = pal.mid().color()
        elif on:
            track = pal.highlight().color()
        else:
            track = pal.mid().color().darker(120)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.right() - d - 3 if on else r.left() + 3
        p.setBrush(QColor("white") if self.isEnabled() else QColor(200, 200, 200))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))


class AdapterRow(QWidget):
    def __init__(self, ifname: str, on_toggle):
        super().__init__()
        self.ifname = ifname
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        text = QVBoxLayout()
        text.setSpacing(1)
        self.title = QLabel()
        f = QFont(self.title.font())
        f.setBold(True)
        self.title.setFont(f)
        self.title.setWordWrap(True)
        self.sub = QLabel()
        self.sub.setEnabled(False)
        self.sub.setWordWrap(True)
        text.addWidget(self.title)
        text.addWidget(self.sub)
        lay.addLayout(text, 1)
        self.switch = ToggleSwitch()
        self.switch.clicked.connect(lambda checked: on_toggle(self.ifname, checked))
        lay.addWidget(self.switch, 0, Qt.AlignVCenter)

    def update_from(self, a: Adapter):
        self.title.setText(a.label)
        self.sub.setText(f"{a.ifname} · {a.status_text}")
        self.switch.blockSignals(True)
        self.switch.setChecked(a.enabled)
        self.switch.setEnabled(not a.hard_blocked and a.rfkill_idx is not None)
        self.switch.blockSignals(False)
        self.switch.update()


class Popup(QWidget):
    def __init__(self, on_toggle):
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint)
        self.setFixedWidth(400)
        self._on_toggle = on_toggle
        self.rows: dict[str, AdapterRow] = {}
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(4, 4, 4, 4)
        header = QLabel("WiFi adapters")
        hf = QFont(header.font())
        hf.setPointSize(hf.pointSize() + 1)
        hf.setBold(True)
        header.setFont(hf)
        header.setContentsMargins(12, 8, 12, 4)
        self.outer.addWidget(header)
        self.list_box = QVBoxLayout()
        self.list_box.setSpacing(0)
        self.outer.addLayout(self.list_box)
        self.empty = QLabel("No WiFi adapters found")
        self.empty.setContentsMargins(12, 8, 12, 12)
        self.empty.setEnabled(False)
        self.outer.addWidget(self.empty)

    def sync(self, adapters: list[Adapter]):
        names = [a.ifname for a in adapters]
        for name in list(self.rows):
            if name not in names:
                row = self.rows.pop(name)
                self.list_box.removeWidget(row)
                row.deleteLater()
        for a in adapters:
            row = self.rows.get(a.ifname)
            if row is None:
                if self.rows:
                    sep = QFrame()
                    sep.setFrameShape(QFrame.HLine)
                    sep.setFrameShadow(QFrame.Sunken)
                    self.list_box.addWidget(sep)
                row = AdapterRow(a.ifname, self._on_toggle)
                self.rows[a.ifname] = row
                self.list_box.addWidget(row)
            row.update_from(a)
        self.empty.setVisible(not adapters)
        self.adjustSize()

    def show_near(self, anchor: QPoint):
        self.adjustSize()
        screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
        g = screen.availableGeometry()
        x = min(max(anchor.x() - self.width() // 2, g.left() + 8), g.right() - self.width() - 8)
        y = anchor.y() - self.height() - 8
        if y < g.top() + 8:
            y = anchor.y() + 16
        y = min(y, g.bottom() - self.height() - 8)
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()


# ------------------------------------------------------------------------- icon
def build_svg(states: list[bool]) -> str:
    """Neon wifi icon. One pip under the arcs per adapter: lit = on, dim = off.
    All adapters off: arcs go grey and get a red slash."""
    import math

    any_on = any(states) if states else False
    cx, cy = 32.0, 38.0
    s45 = math.sin(math.radians(45))
    arcs = []
    for r, w in ((10, 5.5), (20, 5.5), (30, 5.5)):
        x1, y1 = cx - r * s45, cy - r * s45
        x2, y2 = cx + r * s45, cy - r * s45
        arcs.append(
            f'<path d="M{x1:.2f} {y1:.2f} A{r} {r} 0 0 1 {x2:.2f} {y2:.2f}" '
            f'fill="none" stroke="url(#g)" stroke-width="{w}" stroke-linecap="round"/>'
        )
    pips = []
    n = max(len(states), 1)
    xs = [32.0] if n == 1 else [32 - 11 + i * (22 / (n - 1)) for i in range(n)]
    for x, on in zip(xs, states or [False]):
        if on:
            pips.append(f'<circle cx="{x:.1f}" cy="56.5" r="4.6" fill="#34d399"/>')
            pips.append(f'<circle cx="{x:.1f}" cy="56.5" r="7" fill="#34d399" opacity="0.25"/>')
        else:
            pips.append(
                f'<circle cx="{x:.1f}" cy="56.5" r="3.6" fill="none" stroke="#64748b" stroke-width="2"/>'
            )
    stops = (
        '<stop offset="0" stop-color="#22d3ee"/><stop offset="1" stop-color="#a855f7"/>'
        if any_on
        else '<stop offset="0" stop-color="#94a3b8"/><stop offset="1" stop-color="#64748b"/>'
    )
    slash = (
        ""
        if any_on
        else '<line x1="10" y1="10" x2="54" y2="54" stroke="#f87171" stroke-width="5.5" stroke-linecap="round"/>'
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">{stops}</linearGradient></defs>'
        + "".join(arcs)
        + f'<circle cx="{cx}" cy="{cy}" r="4.8" fill="url(#g)"/>'
        + "".join(pips)
        + slash
        + "</svg>"
    )


def make_icon(states: list[bool]) -> QIcon:
    renderer = QSvgRenderer(build_svg(states).encode())
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64, 128):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        renderer.render(p)
        p.end()
        icon.addPixmap(pm)
    return icon


class TrayApp:
    def __init__(self, app: QApplication):
        self.app = app
        self.adapters: list[Adapter] = []
        self.tray = QSystemTrayIcon()
        self.popup = Popup(self.toggle)
        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._activated)
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(REFRESH_MS)
        self.refresh()
        self.tray.show()

    # -- actions
    def toggle(self, ifname: str, want_on: bool):
        a = next((x for x in self.adapters if x.ifname == ifname), None)
        if a is None:
            return
        if not want_on:
            others_on = [x for x in self.adapters if x.ifname != ifname and x.enabled]
            if not others_on:
                ans = QMessageBox.question(
                    None,
                    "Turn off last WiFi adapter?",
                    f"{a.label} is the only adapter that is on.\n"
                    "Turning it off will disconnect WiFi entirely. Continue?",
                )
                if ans != QMessageBox.Yes:
                    self.refresh()
                    return
        err = set_enabled(a, want_on)
        if err:
            self.tray.showMessage("WiFi Tray", f"Could not switch {a.ifname}: {err}",
                                  QSystemTrayIcon.Warning, 5000)
        self.refresh()
        # NetworkManager takes a moment to report the new state
        QTimer.singleShot(1500, self.refresh)

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.popup.isVisible():
                self.popup.hide()
            else:
                self.refresh()
                geo = self.tray.geometry()
                anchor = geo.center() if not geo.isEmpty() else QCursor.pos()
                self.popup.show_near(anchor)

    # -- refresh
    def refresh(self):
        self.adapters = discover()
        self.popup.sync(self.adapters)
        self._rebuild_menu()
        self.tray.setIcon(make_icon([a.enabled for a in self.adapters]))
        lines = [f"{a.ifname}: {a.status_text}" for a in self.adapters] or ["No WiFi adapters"]
        self.tray.setToolTip("WiFi adapters\n" + "\n".join(lines))

    def _rebuild_menu(self):
        self.menu.clear()
        for a in self.adapters:
            act = QAction(f"{a.label}  [{a.ifname}]", self.menu)
            act.setCheckable(True)
            act.setChecked(a.enabled)
            act.setEnabled(not a.hard_blocked and a.rfkill_idx is not None)
            act.triggered.connect(lambda checked, n=a.ifname: self.toggle(n, checked))
            self.menu.addAction(act)
            info = QAction(f"      {a.status_text}", self.menu)
            info.setEnabled(False)
            self.menu.addAction(info)
        if not self.adapters:
            self.menu.addAction("No WiFi adapters").setEnabled(False)
        self.menu.addSeparator()
        self.menu.addAction("Quit", self.app.quit)


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--write-icon":
        with open(sys.argv[2], "w") as f:
            f.write(build_svg([True, True]))
        return 0
    app = QApplication(sys.argv)
    app.setApplicationName("wifi-tray")
    app.setQuitOnLastWindowClosed(False)
    # At login the panel may not be up yet; wait for the tray host (up to 60 s).
    waited = 0
    while not QSystemTrayIcon.isSystemTrayAvailable():
        if waited >= 60:
            print("No system tray available.", file=sys.stderr)
            return 1
        time.sleep(1)
        waited += 1
        app.processEvents()
    runtime = QStandardPaths.writableLocation(QStandardPaths.RuntimeLocation) or "/tmp"
    lock = QLockFile(os.path.join(runtime, "wifi-tray.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        print("wifi-tray is already running.", file=sys.stderr)
        return 0
    app.setWindowIcon(make_icon([True, True]))
    _tray = TrayApp(app)  # keep a reference
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
