"""Tap the phone on the desk to recalibrate.

The phone sends the peak high-pass acceleration per packet; a tap is a spike above
the threshold. Fed from the BLE thread for every packet, so no spike is missed.
"""

import threading

DEBOUNCE = 0.12  # s, one tap rings for a few packets
DOUBLE_WINDOW = 0.6  # s between the taps of a double tap
SETTLE = 0.3  # s after the (last) tap before calibrating
MUTE = 0.8  # s of zero output after a tap, so the knock does not move the model


class TapDetector:
    def __init__(self, cfg):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._last_tap = -1e9
        self._prev_tap = -1e9
        self.calibrate_at = None
        self.mute_until = 0.0
        self.peak = 0.0  # for the live meter

    def feed(self, peak, t):
        with self._lock:
            self.peak = max(self.peak, peak)
            mode = self.cfg["tap_calibrate"]
            if mode == "off" or peak < self.cfg["tap_threshold"] or t - self._last_tap < DEBOUNCE:
                return
            self._prev_tap, self._last_tap = self._last_tap, t
            self.mute_until = t + MUTE
            if mode == "single" or t - self._prev_tap <= DOUBLE_WINDOW:
                self.calibrate_at = t + SETTLE
                self._last_tap = -1e9  # a third tap starts over

    def take_peak(self):
        with self._lock:
            p, self.peak = self.peak, 0.0
            return p

    def due(self, now) -> bool:
        """True once when it is time to calibrate."""
        with self._lock:
            if self.calibrate_at is not None and now >= self.calibrate_at:
                self.calibrate_at = None
                return True
            return False
