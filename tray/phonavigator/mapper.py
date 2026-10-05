"""From phone orientation to SpaceMouse deflection (rate control).

The phone stands upright on its charging-port edge: Android device Y points up,
Z points out of the screen, X to the right of the screen.

- pitch: tilt forward/back as seen from the phone (about device X)
- roll:  tilt sideways as seen from the phone (about device Z)
- twist: turn about the vertical axis

Pitch and roll come from gravity and do not drift. Twist has no reference (no
magnetometer), so its zero is reset every time the phone is picked up and slowly
follows the phone while it rests inside the deadzone.
"""

import math

from .config import INPUTS

AXES = ("TX", "TY", "TZ", "RX", "RY", "RZ")
FULL_SCALE = 350  # range of a real SpaceMouse
STALE_AFTER = 0.3  # s without data = stand still
RECENTER_TAU = 2.0  # s


def _wrap(deg):
    return (deg + 180.0) % 360.0 - 180.0


def _norm(v):
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / n for c in v]


def _up_and_heading(q):
    """Up vector in device coordinates and the heading of device X (degrees)."""
    x, y, z, w = q
    # Rotation matrix device -> world; row 2 = world up expressed in device coordinates.
    r00 = 1 - 2 * (y * y + z * z)
    r10 = 2 * (x * y + z * w)
    up = _norm([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)])
    # Device X stays horizontal under both pitch and roll, so its heading is a clean twist measure.
    heading = math.degrees(math.atan2(r10, r00))
    return up, heading


def _flip(up, heading):
    """Phone held upside down (charging port up, screen facing the same way): undo the
    180° turn about the screen normal, so all movements feel the same as port down."""
    return [-up[0], -up[1], up[2]], _wrap(heading + 180.0)


def _tilts(up):
    ux, uy, uz = up
    return math.degrees(math.atan2(uz, uy)), math.degrees(math.atan2(ux, uy))


def _angle_between(a, b):
    dot = max(-1.0, min(1.0, sum(i * j for i, j in zip(a, b))))
    return math.degrees(math.acos(dot))


class Mapper:
    def __init__(self, cfg):
        self.cfg = cfg
        self.q = None
        self.q_time = None
        self.twist0 = None
        self.upright = False
        self.flipped = False
        self.angles = {k: 0.0 for k in INPUTS}
        self.covered = False
        self.state = "nodata"  # nodata | covered | flat | active

    def feed(self, q, now, covered=False):
        self.q = q
        self.q_time = now
        self.covered = covered

    def _pose(self):
        """Up vector and heading in the 'port down' frame, following the charging-port setting."""
        up, heading = _up_and_heading(self.q)
        mode = self.cfg["charging_port"]
        if mode == "auto":
            # Decide only while not in use, so it never flips mid-movement.
            flipped = up[1] < 0 if not self.upright else self.flipped
        else:
            flipped = mode == "up"
        if flipped != self.flipped:
            self.flipped = flipped
            self.upright = False  # re-zero twist in the new frame
        return _flip(up, heading) if flipped else (up, heading)

    def calibrate(self):
        """Make the current position neutral. False if there is no data yet."""
        if self.q is None:
            return False
        up, heading = self._pose()
        self.cfg["neutral_up"] = up
        self.twist0 = heading
        self.upright = True
        return True

    def compute(self, now, dt):
        """Six axes, ints within ±FULL_SCALE."""
        out = dict.fromkeys(AXES, 0.0)
        if self.q is None or now - self.q_time > STALE_AFTER:
            self.state = "nodata"
            self.angles = dict.fromkeys(INPUTS, 0.0)
            return [0] * 6

        if self.covered:  # in a pocket or face down: pause, re-zero twist when picked up
            self.upright = False
            self.state = "covered"
            self.angles = dict.fromkeys(INPUTS, 0.0)
            return [0] * 6

        up, heading = self._pose()
        up0 = self.cfg["neutral_up"]
        tilt = _angle_between(up, up0)
        pause = self.cfg["pause_angle"]

        # Laying the phone down pauses; picking it up again re-zeroes twist.
        if self.upright and tilt > pause:
            self.upright = False
        elif not self.upright and tilt < pause * 0.6:
            self.upright = True
            self.twist0 = heading
        if not self.upright:
            self.state = "flat"
            self.angles = dict.fromkeys(INPUTS, 0.0)
            return [0] * 6
        self.state = "active"

        p, r = _tilts(up)
        p0, r0 = _tilts(up0)
        twist = _wrap(heading - self.twist0)
        # Signs chosen so the shipped defaults need no "invert": the top of the phone tilting
        # towards the screen side and turning clockwise seen from above are positive.
        self.angles = {
            "pitch": -_wrap(p - p0),
            "roll": _wrap(r - r0),
            "twist": -twist,
        }

        inputs = self.cfg["inputs"]
        if self.cfg["auto_recenter_twist"] and all(
            abs(self.angles[k]) < inputs[k]["deadzone"] for k in INPUTS
        ):
            self.twist0 = _wrap(self.twist0 + twist * min(1.0, dt / RECENTER_TAU))

        scale = FULL_SCALE * self.cfg["speed"]
        for name in INPUTS:
            ic = inputs[name]
            if ic["target"] not in out:
                continue
            a = self.angles[name]
            span = max(0.1, ic["max"] - ic["deadzone"])
            x = min(1.0, max(0.0, (abs(a) - ic["deadzone"]) / span))
            v = math.copysign(x ** ic["expo"], a) * scale
            out[ic["target"]] += -v if ic["invert"] else v

        return [int(round(max(-FULL_SCALE, min(FULL_SCALE, out[k])))) for k in AXES]
