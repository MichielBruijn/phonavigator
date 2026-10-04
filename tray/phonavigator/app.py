import signal
import sys
import threading
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QMenu, QMessageBox, QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from . import __version__, config, outputs
from .ble import BleLink
from .mapper import AXES, FULL_SCALE, Mapper

TICK_MS = 16  # ~60 Hz naar spacenavd

INPUT_LABELS = {
    "pitch": "Kantelen voor/achter",
    "roll": "Kantelen opzij",
    "twist": "Draaien (staande as)",
}
TARGET_LABELS = {
    "off": "Uit",
    "TX": "Schuiven X", "TY": "Schuiven Y", "TZ": "Schuiven Z",
    "RX": "Draaien RX", "RY": "Draaien RY", "RZ": "Draaien RZ",
}
STATE_COLORS = {"nodata": "#8a8f98", "flat": "#e0a030", "active": "#3cb371", "paused": "#e0a030"}
STATE_TEXT = {
    "nodata": "Geen data",
    "flat": "Pauze (telefoon ligt)",
    "active": "Actief",
    "paused": "Gepauzeerd",
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
    """Balk vanuit het midden; voor hoeken en asuitslag."""

    def __init__(self, span):
        super().__init__()
        self.span = span
        self.value = 0.0
        self.setMinimumSize(160, 14)

    def set(self, v):
        if v != self.value:
            self.value = v
            self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        r = self.rect().adjusted(0, 2, -1, -2)
        p.fillRect(r, self.palette().base())
        mid = r.center().x()
        frac = max(-1.0, min(1.0, self.value / self.span))
        w = int(frac * r.width() / 2)
        bar = QRectF(min(mid, mid + w), r.top(), abs(w), r.height())
        p.fillRect(bar, QColor("#3c8dde"))
        p.setPen(self.palette().mid().color())
        p.drawRect(r)
        p.drawLine(mid, r.top(), mid, r.bottom())


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

        # Per telefoonbeweging: doel-as, richting, deadzone, max, curve
        g = QGroupBox("Bewegingen")
        grid = QGridLayout(g)
        heads = ["", "Live (°)", "Naar as", "Omdraaien", "Deadzone °", "Vol gas bij °", "Curve"]
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

        # Algemeen
        g = QGroupBox("Algemeen")
        h = QHBoxLayout(g)
        h.addWidget(QLabel("Snelheid"))
        speed = QDoubleSpinBox()
        speed.setRange(0.05, 1.0)
        speed.setSingleStep(0.05)
        speed.setValue(cfg["speed"])
        speed.valueChanged.connect(lambda v: self._set_global("speed", v))
        h.addWidget(speed)
        h.addSpacing(16)
        h.addWidget(QLabel("Pauze vanaf °"))
        pause = QDoubleSpinBox()
        pause.setRange(20, 85)
        pause.setDecimals(0)
        pause.setValue(cfg["pause_angle"])
        pause.valueChanged.connect(lambda v: self._set_global("pause_angle", v))
        h.addWidget(pause)
        h.addSpacing(16)
        rec = QCheckBox("Draai-nulstand laten meeschuiven")
        rec.setToolTip("Corrigeert drift van de draai-as zolang de telefoon in de deadzone staat")
        rec.setChecked(cfg["auto_recenter_twist"])
        rec.toggled.connect(lambda v: self._set_global("auto_recenter_twist", v))
        h.addWidget(rec)
        h.addStretch()
        root.addWidget(g)

        # Uitvoer
        g = QGroupBox("Uitvoer naar 3D-applicatie")
        grid = QGridLayout(g)
        self.out_bars = []
        for i, axis in enumerate(AXES):
            grid.addWidget(QLabel(axis), i // 3, (i % 3) * 2)
            bar = CenterBar(FULL_SCALE)
            self.out_bars.append(bar)
            grid.addWidget(bar, i // 3, (i % 3) * 2 + 1)
        root.addWidget(g)

        h = QHBoxLayout()
        self.cal_btn = QPushButton("Nulstand kalibreren")
        self.cal_btn.clicked.connect(ctl.calibrate_delayed)
        h.addWidget(self.cal_btn)
        h.addStretch()
        h.addWidget(QLabel(f"v{__version__}"))
        root.addLayout(h)

    def _set(self, name, key, value):
        self.ctl.cfg["inputs"][name][key] = value
        self.ctl.save_later()

    def _set_global(self, key, value):
        self.ctl.cfg[key] = value
        self.ctl.save_later()

    def refresh(self, values):
        m = self.ctl.mapper
        for name, bar in self.angle_bars.items():
            bar.set(m.angles[name])
            bar.setToolTip(f"{m.angles[name]:+.1f}°")
        for bar, v in zip(self.out_bars, values):
            bar.set(v)
        self.status.setText(
            f"<b>{STATE_TEXT[self.ctl.state()]}</b> · {self.ctl.link_status} · {self.ctl.backend_status}")


class Controller:
    def __init__(self, app):
        self.app = app
        self.cfg = config.load()
        self.mapper = Mapper(self.cfg)
        self.paused = False
        self.link_status = "Starten…"
        self.window = None
        self.values = [0] * 6
        self._sample = None
        self._sample_lock = threading.Lock()
        self._last_tick = time.monotonic()
        self._shown_state = None
        self._cal_countdown = 0

        try:
            self.backend = outputs.create()
            self.backend_status = f"uitvoer: {self.backend.name}"
        except outputs.BackendError as ex:
            self.backend = None
            self.backend_status = f"uitvoer: {ex}"

        self.tray = QSystemTrayIcon(make_icon(STATE_COLORS["nodata"]))
        self.tray.setToolTip("Phonavigator")
        menu = QMenu()
        self.status_action = menu.addAction(self.link_status)
        self.status_action.setEnabled(False)
        menu.addSeparator()
        self.pause_action = QAction("Pauze", menu, checkable=True)
        self.pause_action.toggled.connect(self._set_paused)
        menu.addAction(self.pause_action)
        menu.addAction("Nulstand kalibreren (over 2 s)", self.calibrate_delayed)
        menu.addAction("Nu zoeken naar telefoon", lambda: self.link.rescan())
        menu.addAction("Instellingen…", self.show_window)
        menu.addSeparator()
        menu.addAction("Afsluiten", self.quit)
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
        self.cal_timer = QTimer(interval=1000)
        self.cal_timer.timeout.connect(self._cal_step)

        self.timer = QTimer(timerType=Qt.PreciseTimer, interval=TICK_MS)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._n = 0

        if self.backend is None:
            self.tray.showMessage("Phonavigator", self.backend_status, QSystemTrayIcon.Warning)

    # Aangeroepen vanuit de BLE-thread
    def _on_sample(self, q, t):
        with self._sample_lock:
            self._sample = (q, t)

    def _on_link_status(self, s):
        self.link_status = s
        self.status_action.setText(s)

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
        values = self.mapper.compute(now, dt)
        if self.paused:
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

    def calibrate_delayed(self):
        # Even tijd om de muis los te laten en de telefoon in de prettige stand te zetten.
        self._cal_countdown = 2
        self._cal_step()
        self.cal_timer.start()

    def _cal_step(self):
        if self._cal_countdown > 0:
            self._set_cal_text(f"Kalibreren over {self._cal_countdown}…")
            self._cal_countdown -= 1
            return
        self.cal_timer.stop()
        if self.mapper.calibrate():
            config.save(self.cfg)
            self.tray.showMessage("Phonavigator", "Nulstand opgeslagen", QSystemTrayIcon.Information, 1500)
        else:
            self.tray.showMessage("Phonavigator", "Geen data van de telefoon", QSystemTrayIcon.Warning)
        self._set_cal_text("Nulstand kalibreren")

    def _set_cal_text(self, text):
        if self.window:
            self.window.cal_btn.setText(text)

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


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Phonavigator")
    app.setDesktopFileName("phonavigator")
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "Phonavigator", "Geen systeemvak beschikbaar.")
        return 1
    ctl = Controller(app)
    # Ctrl+C in de terminal netjes afhandelen
    signal.signal(signal.SIGINT, lambda *_: ctl.quit())
    ctl._sigtimer = QTimer(interval=250)
    ctl._sigtimer.timeout.connect(lambda: None)
    ctl._sigtimer.start()
    if "--settings" in sys.argv:
        ctl.show_window()
    return app.exec()
