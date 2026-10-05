import getpass
import signal
import sys
import threading
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QMenu, QMessageBox, QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from . import __version__, config, outputs
from .ble import BleLink
from .mapper import AXES, FULL_SCALE, Mapper
from .tap import TapDetector

try:
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
except ImportError:  # e.g. python3-pyside6.qtnetwork not installed: no single-instance guard
    QLocalServer = QLocalSocket = None

TICK_MS = 16  # ~60 Hz to spacenavd
PROJECT_URL = "https://github.com/MichielBruijn/phonavigator"

INPUT_LABELS = {
    "pitch": "Tilt forward/back",
    "roll": "Tilt sideways",
    "twist": "Twist (vertical axis)",
}
TARGET_LABELS = {
    "off": "Off",
    "TX": "Move X", "TY": "Move Y", "TZ": "Move Z",
    "RX": "Rotate RX", "RY": "Rotate RY", "RZ": "Rotate RZ",
}
PORT_MODES = {"auto": "Auto", "down": "Down", "up": "Up (charging)"}
TAP_MODES = {"off": "Off", "single": "Single tap", "double": "Double tap"}
STATE_COLORS = {"nodata": "#8a8f98", "flat": "#e0a030", "active": "#3cb371", "paused": "#e0a030"}
STATE_TEXT = {
    "nodata": "No data",
    "flat": "Paused (phone lying down)",
    "active": "Active",
    "paused": "Paused",
}


def make_icon(color: str) -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(20, 6, 24, 46), 5, 5)
    p.setBrush(QColor("#1e2530"))
    p.drawRect(QRectF(23, 11, 18, 34))
    pen = QPen(QColor(color), 4, Qt.SolidLine, Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    arc = QPainterPath()
    arc.moveTo(8, 48)
    arc.cubicTo(14, 62, 50, 62, 56, 48)
    p.drawPath(arc)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawPolygon([QPointF(56, 40), QPointF(62, 51), QPointF(50, 51)])
    p.end()
    return QIcon(pm)


class CenterBar(QWidget):
    """Bar growing from the middle (or from the left when one_sided); for angles and axis values."""

    def __init__(self, span, one_sided=False):
        super().__init__()
        self.span = span
        self.one_sided = one_sided
        self.value = 0.0
        self.marker = None
        self.setMinimumSize(160, 14)

    def set(self, v):
        if v != self.value:
            self.value = v
            self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        r = self.rect().adjusted(0, 2, -1, -2)
        p.fillRect(r, self.palette().base())
        if self.one_sided:
            w = int(min(1.0, max(0.0, self.value / self.span)) * r.width())
            p.fillRect(QRectF(r.left(), r.top(), w, r.height()), QColor("#3c8dde"))
        else:
            mid = r.center().x()
            frac = max(-1.0, min(1.0, self.value / self.span))
            w = int(frac * r.width() / 2)
            p.fillRect(QRectF(min(mid, mid + w), r.top(), abs(w), r.height()), QColor("#3c8dde"))
        p.setPen(self.palette().mid().color())
        p.drawRect(r)
        if self.one_sided:
            if self.marker is not None:
                x = r.left() + int(min(1.0, self.marker / self.span) * r.width())
                p.setPen(QPen(QColor("#d04040"), 2))
                p.drawLine(x, r.top(), x, r.bottom())
        else:
            p.drawLine(r.center().x(), r.top(), r.center().x(), r.bottom())


class SettingsWindow(QWidget):
    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl
        self.setWindowTitle("Phonavigator")
        self.setWindowIcon(make_icon(STATE_COLORS["active"]))
        cfg = ctl.cfg
        root = QVBoxLayout(self)

        self.status = QLabel()
        root.addWidget(self.status)

        # Per phone movement: target axis, direction, deadzone, full-speed angle, curve
        g = QGroupBox("Movements")
        grid = QGridLayout(g)
        heads = ["", "Live (°)", "Axis", "Invert", "Deadzone °", "Full speed at °", "Curve"]
        for c, h in enumerate(heads):
            grid.addWidget(QLabel(f"<b>{h}</b>"), 0, c)
        self.angle_bars = {}
        for row, name in enumerate(config.INPUTS, start=1):
            ic = cfg["inputs"][name]
            grid.addWidget(QLabel(INPUT_LABELS[name]), row, 0)
            bar = CenterBar(45)
            self.angle_bars[name] = bar
            grid.addWidget(bar, row, 1)

            combo = QComboBox()
            for t in config.TARGETS:
                combo.addItem(TARGET_LABELS[t], t)
            combo.setCurrentIndex(config.TARGETS.index(ic["target"]))
            combo.currentIndexChanged.connect(
                lambda _i, n=name, c=combo: self._set(n, "target", c.currentData()))
            grid.addWidget(combo, row, 2)

            inv = QCheckBox()
            inv.setChecked(ic["invert"])
            inv.toggled.connect(lambda v, n=name: self._set(n, "invert", v))
            grid.addWidget(inv, row, 3, Qt.AlignCenter)

            for col, key, lo, hi, step, dec in (
                (4, "deadzone", 0, 30, 0.5, 1), (5, "max", 1, 80, 1, 0), (6, "expo", 1, 4, 0.1, 1),
            ):
                sb = QDoubleSpinBox()
                sb.setRange(lo, hi)
                sb.setSingleStep(step)
                sb.setDecimals(dec)
                sb.setValue(ic[key])
                sb.valueChanged.connect(lambda v, n=name, k=key: self._set(n, k, v))
                grid.addWidget(sb, row, col)
        root.addWidget(g)

        # General
        g = QGroupBox("General")
        h = QHBoxLayout(g)
        h.addWidget(QLabel("Speed"))
        speed = QDoubleSpinBox()
        speed.setRange(0.05, 1.0)
        speed.setSingleStep(0.05)
        speed.setValue(cfg["speed"])
        speed.valueChanged.connect(lambda v: self._set_global("speed", v))
        h.addWidget(speed)
        h.addSpacing(16)
        h.addWidget(QLabel("Pause beyond °"))
        pause = QDoubleSpinBox()
        pause.setRange(20, 85)
        pause.setDecimals(0)
        pause.setValue(cfg["pause_angle"])
        pause.valueChanged.connect(lambda v: self._set_global("pause_angle", v))
        h.addWidget(pause)
        h.addSpacing(16)
        h.addWidget(QLabel("Charging port"))
        port = QComboBox()
        for k, label in PORT_MODES.items():
            port.addItem(label, k)
        port.setCurrentIndex(list(PORT_MODES).index(cfg["charging_port"]))
        port.currentIndexChanged.connect(lambda _i: self._set_global("charging_port", port.currentData()))
        h.addWidget(port)
        h.addSpacing(16)
        rec = QCheckBox("Let twist zero follow")
        rec.setToolTip("Corrects drift of the twist axis while the phone rests inside the deadzone")
        rec.setChecked(cfg["auto_recenter_twist"])
        rec.toggled.connect(lambda v: self._set_global("auto_recenter_twist", v))
        h.addWidget(rec)
        h.addStretch()
        root.addWidget(g)

        # Tap to calibrate
        g = QGroupBox("Tap the phone on the desk to calibrate")
        h = QHBoxLayout(g)
        mode = QComboBox()
        for k, label in TAP_MODES.items():
            mode.addItem(label, k)
        mode.setCurrentIndex(list(TAP_MODES).index(cfg["tap_calibrate"]))
        mode.currentIndexChanged.connect(lambda _i: self._set_global("tap_calibrate", mode.currentData()))
        h.addWidget(mode)
        h.addSpacing(16)
        h.addWidget(QLabel("Threshold m/s²"))
        thr = QDoubleSpinBox()
        thr.setRange(3, 120)
        thr.setSingleStep(1)
        thr.setDecimals(0)
        thr.setValue(cfg["tap_threshold"])
        thr.valueChanged.connect(self._set_threshold)
        h.addWidget(thr)
        h.addSpacing(16)
        h.addWidget(QLabel("Peak"))
        self.tap_bar = CenterBar(60, one_sided=True)
        self.tap_bar.marker = cfg["tap_threshold"]
        self.tap_bar.setToolTip("Peak acceleration; a tap must cross the red line")
        h.addWidget(self.tap_bar, 1)
        root.addWidget(g)

        # Output
        g = QGroupBox("Output to 3D application")
        grid = QGridLayout(g)
        self.out_bars = []
        for i, axis in enumerate(AXES):
            grid.addWidget(QLabel(axis), i // 3, (i % 3) * 2)
            bar = CenterBar(FULL_SCALE)
            self.out_bars.append(bar)
            grid.addWidget(bar, i // 3, (i % 3) * 2 + 1)
        root.addWidget(g)

        h = QHBoxLayout()
        cal = QPushButton("Calibrate neutral position")
        cal.clicked.connect(ctl.calibrate)
        h.addWidget(cal)
        h.addStretch()
        link = QLabel(f'<a href="{PROJECT_URL}">Phonavigator v{__version__} on GitHub</a>')
        link.setOpenExternalLinks(True)
        h.addWidget(link)
        root.addLayout(h)
        self._peak_hold = 0.0

    def _set(self, name, key, value):
        self.ctl.cfg["inputs"][name][key] = value
        self.ctl.save_later()

    def _set_global(self, key, value):
        self.ctl.cfg[key] = value
        self.ctl.save_later()

    def _set_threshold(self, v):
        self._set_global("tap_threshold", v)
        self.tap_bar.marker = v
        self.tap_bar.update()

    def refresh(self, values):
        m = self.ctl.mapper
        for name, bar in self.angle_bars.items():
            bar.set(m.angles[name])
            bar.setToolTip(f"{m.angles[name]:+.1f}°")
        for bar, v in zip(self.out_bars, values):
            bar.set(v)
        # Hold the peak briefly so a tap is visible at all.
        self._peak_hold = max(self.ctl.taps.take_peak(), self._peak_hold * 0.85)
        self.tap_bar.set(self._peak_hold)
        self.status.setText(
            f"<b>{STATE_TEXT[self.ctl.state()]}</b> · {self.ctl.link_status} · {self.ctl.output_status()}")


class Controller:
    def __init__(self, app):
        self.app = app
        self.cfg = config.load()
        self.mapper = Mapper(self.cfg)
        self.taps = TapDetector(self.cfg)
        self.paused = False
        self.link_status = "Starting…"
        self.window = None
        self.values = [0] * 6
        self._sample = None
        self._sample_lock = threading.Lock()
        self._last_tick = time.monotonic()
        self._shown_state = None

        try:
            self.backend = outputs.create()
            self.backend_status = f"output: {self.backend.name}"
        except outputs.BackendError as ex:
            self.backend = None
            self.backend_status = f"output: {ex}"

        self.tray = QSystemTrayIcon(make_icon(STATE_COLORS["nodata"]))
        self.tray.setToolTip("Phonavigator")
        menu = QMenu()
        self.status_action = menu.addAction(self.link_status)
        self.status_action.setEnabled(False)
        menu.addSeparator()
        self.pause_action = QAction("Pause", menu, checkable=True)
        self.pause_action.toggled.connect(self._set_paused)
        menu.addAction(self.pause_action)
        menu.addAction("Calibrate neutral position", self.calibrate)
        menu.addAction("Look for phone now", lambda: self.link.rescan())
        menu.addAction("Settings…", self.show_window)
        menu.addAction("Phonavigator on GitHub", lambda: QDesktopServices.openUrl(QUrl(PROJECT_URL)))
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.show_window() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()
        self._menu = menu

        self.link = BleLink(self._on_sample)
        self.link.status.connect(self._on_link_status)
        self.link.start()

        self.save_timer = QTimer(singleShot=True, interval=500)
        self.save_timer.timeout.connect(lambda: config.save(self.cfg))

        self.timer = QTimer(timerType=Qt.PreciseTimer, interval=TICK_MS)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._n = 0

        if self.backend is None:
            self.tray.showMessage("Phonavigator", self.backend_status, QSystemTrayIcon.Warning)

    # Called from the BLE thread, for every packet
    def _on_sample(self, q, peak, t):
        self.taps.feed(peak, t)
        with self._sample_lock:
            self._sample = (q, t)

    def _on_link_status(self, s):
        print(s, flush=True)  # ends up in the journal when run as a service
        self.link_status = s
        self.status_action.setText(s)

    def output_status(self):
        return f"output: {self.backend.name}" if self.backend else self.backend_status

    def _set_paused(self, on):
        self.paused = on

    def state(self):
        return "paused" if self.paused and self.mapper.state == "active" else self.mapper.state

    def _tick(self):
        now = time.monotonic()
        dt, self._last_tick = now - self._last_tick, now
        with self._sample_lock:
            s = self._sample
        if s:
            self.mapper.feed(*s)
        if self.taps.due(now):
            self.calibrate()
        values = self.mapper.compute(now, dt)
        if self.paused or now < self.taps.mute_until:
            values = [0] * 6
        self.values = values
        if self.backend:
            self.backend.write(values)

        st = self.state()
        if st != self._shown_state:
            self._shown_state = st
            self.tray.setIcon(make_icon(STATE_COLORS[st]))
            self.tray.setToolTip(f"Phonavigator – {STATE_TEXT[st]}")

        self._n += 1
        if self.window and self.window.isVisible() and self._n % 3 == 0:
            self.window.refresh(values)

    def calibrate(self):
        if self.mapper.calibrate():
            config.save(self.cfg)
        else:
            self.tray.showMessage("Phonavigator", "No data from the phone", QSystemTrayIcon.Warning)

    def save_later(self):
        self.save_timer.start()

    def show_window(self):
        if self.window is None:
            self.window = SettingsWindow(self)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def quit(self):
        self.timer.stop()
        self.link.stop()
        if self.backend:
            self.backend.close()
        config.save(self.cfg)
        self.app.quit()


INSTANCE = f"phonavigator-{getpass.getuser()}"


def _hand_over_to_running_instance() -> bool:
    """True if another tray is running; it is asked to show its window instead."""
    if QLocalSocket is None:
        return False
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE)
    if not sock.waitForConnected(500):
        return False
    sock.write(b"show")
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    return True


def _listen_for_other_instances(ctl):
    if QLocalServer is None:
        return None
    QLocalServer.removeServer(INSTANCE)  # stale socket after a crash
    server = QLocalServer()
    server.listen(INSTANCE)

    def on_connection():
        sock = server.nextPendingConnection()
        sock.readyRead.connect(lambda: sock.readAll() and ctl.show_window())
        sock.disconnected.connect(sock.deleteLater)

    server.newConnection.connect(on_connection)
    return server


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Phonavigator")
    app.setDesktopFileName("phonavigator")
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "Phonavigator", "No system tray available.")
        return 1
    # One tray only: two would fight over the phone and create two virtual SpaceMice.
    if _hand_over_to_running_instance():
        return 0
    ctl = Controller(app)
    ctl._instance_server = _listen_for_other_instances(ctl)
    # Handle Ctrl+C in the terminal cleanly
    signal.signal(signal.SIGINT, lambda *_: ctl.quit())
    ctl._sigtimer = QTimer(interval=250)
    ctl._sigtimer.timeout.connect(lambda: None)
    ctl._sigtimer.start()
    if "--settings" in sys.argv:
        ctl.show_window()
    return app.exec()
