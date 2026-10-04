import copy
import json
import os
import sys
from pathlib import Path

INPUTS = ("pitch", "roll", "twist")
TARGETS = ("off", "TX", "TY", "TZ", "RX", "RY", "RZ")

DEFAULTS = {
    # Up vector in phone coordinates at the neutral position; (0,1,0) = perfectly upright.
    "neutral_up": [0.0, 1.0, 0.0],
    # Raw SpaceMouse axes. Tuned for the phone upright in the left hand, screen facing right:
    # sideways tilt rotates the model about X, forward/back tilt about Y (Z-up CAD, e.g. TopSolid).
    "inputs": {
        "pitch": {"target": "RZ", "invert": False, "deadzone": 7.0, "max": 30.0, "expo": 1.6},
        "roll": {"target": "RX", "invert": False, "deadzone": 7.0, "max": 30.0, "expo": 1.6},
        "twist": {"target": "RY", "invert": False, "deadzone": 7.0, "max": 30.0, "expo": 1.6},
    },
    "speed": 0.4,
    "pause_angle": 50.0,
    "auto_recenter_twist": True,
    # Tap the phone on the desk to make the current position neutral: "off" | "single" | "double"
    "tap_calibrate": "off",
    "tap_threshold": 15.0,  # m/s², high-pass peak
}


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "phonavigator"


def _merge(base, override):
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        elif k in base:
            base[k] = v
    return base


def load() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    try:
        with open(config_dir() / "config.json") as f:
            _merge(cfg, json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg: dict):
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "config.json.tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, d / "config.json")
