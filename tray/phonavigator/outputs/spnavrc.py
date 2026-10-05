"""Undo the axis options in /etc/spnavrc, so the same tray settings work on every machine.

spacenavd turns raw axis i into output axis a with a sign, in three steps (spacenavd 1.3):
  1. device flags of a 3Dconnexion device: swap Y/Z, then negate Y and Z
  2. axismapN, then swap-yz
  3. invert-rot / invert-trans
That is a signed permutation; the tray settings are tuned for spacenavd without spnavrc, so
the backend sends values that end up where a default spacenavd would put them.
Sensitivity and dead zones are left alone: they only change speed, not direction.
"""

import os

PATH = "/etc/spnavrc"
_SWAP = (0, 2, 1, 3, 5, 4)
_IDENTITY = list(range(6))


def parse(text: str) -> dict:
    cfg = {"map": list(_IDENTITY), "swapyz": False, "invert": [False] * 6}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        key, val = (s.strip() for s in line.split("=", 1))
        if key == "swap-yz":
            cfg["swapyz"] = val in ("true", "on", "yes") or (val.isdigit() and int(val) != 0)
        elif key in ("invert-rot", "invert-trans"):
            base = 3 if key == "invert-rot" else 0
            for i, c in enumerate("xyz"):
                if c in val:
                    cfg["invert"][base + i] = True
        elif key.startswith("axismap") and key[7:].isdigit() and val.lstrip("-").isdigit():
            src, dst = int(key[7:]), int(val)
            if 0 <= src < 6 and 0 <= dst < 6:
                cfg["map"][src] = dst
    return cfg


def _chain(cfg):
    """Per raw axis: (output axis, sign) as spacenavd computes it."""
    out = []
    for i in range(6):
        j = _SWAP[i]
        sign = 1 if j in (0, 3) else -1
        axis = cfg["map"][j]
        if cfg["swapyz"]:
            axis = _SWAP[axis]
        out.append((axis, -sign if cfg["invert"][axis] else sign))
    return out


def compensation(cfg):
    """Function raw values -> raw values that makes `cfg` behave like no spnavrc.

    None when nothing needs undoing or the mapping cannot be undone (two inputs on one axis).
    """
    want = _chain(parse(""))
    have = _chain(cfg)
    if want == have:
        return None
    by_axis = {axis: (i, sign) for i, (axis, sign) in enumerate(have)}
    if len(by_axis) != 6:
        return None
    plan = []  # (source raw index, target raw index, sign)
    for i, (axis, sign) in enumerate(want):
        j, sign_c = by_axis[axis]
        plan.append((i, j, sign * sign_c))

    def apply(values):
        out = [0] * 6
        for i, j, s in plan:
            out[j] = s * values[i]
        return out

    return apply


class Watcher:
    """Re-reads spnavrc when it changes (spacenavd picks up edits too)."""

    def __init__(self, path=PATH):
        self.path = path
        self._mtime = None
        self.fn = None
        self.check()

    def check(self):
        try:
            mtime = os.stat(self.path).st_mtime
        except OSError:
            mtime = 0
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            with open(self.path) as f:
                text = f.read()
        except OSError:
            text = ""
        self.fn = compensation(parse(text))
