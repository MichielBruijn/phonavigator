"""Van telefoon-oriëntatie naar SpaceMouse-uitslag (rate-besturing).

Telefoon staat rechtop op de laadpoortkant: Android-device-Y wijst omhoog,
Z wijst uit het scherm naar de gebruiker, X naar rechts.

- pitch: kantelen naar je toe / van je af (om device-X)
- roll:  opzij kantelen (om device-Z)
- twist: draaien om de staande as

Pitch en roll komen uit de zwaartekracht en driften niet. Twist heeft geen
referentie (geen magnetometer), dus de nulstand daarvan wordt bij elke keer
oppakken opnieuw gezet en schuift binnen de deadzone langzaam mee.
"""

import math

from .config import INPUTS

AXES = ("TX", "TY", "TZ", "RX", "RY", "RZ")
FULL_SCALE = 350  # bereik van een echte SpaceMouse
STALE_AFTER = 0.3  # s zonder data = stilstaan
RECENTER_TAU = 2.0  # s


def _wrap(deg):
    return (deg + 180.0) % 360.0 - 180.0


def _norm(v):
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / n for c in v]


def _up_and_heading(q):
    """Up-vector in device-coördinaten en de kompasrichting van device-X (graden)."""
    x, y, z, w = q
    # Rotatiematrix device -> wereld; rij 2 = wereld-omhoog uitgedrukt in device-coördinaten.
    r00 = 1 - 2 * (y * y + z * z)
    r10 = 2 * (x * y + z * w)
    up = _norm([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)])
    # Device-X blijft horizontaal bij pitch én roll, dus zijn richting is een schone twist-maat.
    heading = math.degrees(math.atan2(r10, r00))
    return up, heading


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
        self.angles = {k: 0.0 for k in INPUTS}
        self.state = "nodata"  # nodata | flat | active

    def feed(self, q, now):
        self.q = q
        self.q_time = now

    def calibrate(self):
        """Huidige stand wordt de nulstand. Geeft False als er nog geen data is."""
        if self.q is None:
            return False
        up, heading = _up_and_heading(self.q)
        self.cfg["neutral_up"] = up
        self.twist0 = heading
        self.upright = True
        return True

    def compute(self, now, dt):
        """Zes assen, ints in ±FULL_SCALE."""
        out = dict.fromkeys(AXES, 0.0)
        if self.q is None or now - self.q_time > STALE_AFTER:
            self.state = "nodata"
            self.angles = dict.fromkeys(INPUTS, 0.0)
            return [0] * 6

        up, heading = _up_and_heading(self.q)
        up0 = self.cfg["neutral_up"]
        tilt = _angle_between(up, up0)
        pause = self.cfg["pause_angle"]

        # Plat neerleggen = pauze; weer oppakken = twist opnieuw op nul.
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
        self.angles = {
            "pitch": _wrap(p - p0),
            "roll": _wrap(r - r0),
            "twist": _wrap(heading - self.twist0),
        }

        inputs = self.cfg["inputs"]
        if self.cfg["auto_recenter_twist"] and all(
            abs(self.angles[k]) < inputs[k]["deadzone"] for k in INPUTS
        ):
            self.twist0 = _wrap(self.twist0 + self.angles["twist"] * min(1.0, dt / RECENTER_TAU))

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
