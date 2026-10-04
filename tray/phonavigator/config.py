import copy
import json
import os
import sys
from pathlib import Path

INPUTS = ("pitch", "roll", "twist")
TARGETS = ("off", "TX", "TY", "TZ", "RX", "RY", "RZ")

DEFAULTS = {
    # Up-vector in telefooncoördinaten bij de nulstand; (0,1,0) = kaarsrecht.
    "neutral_up": [0.0, 1.0, 0.0],
    "inputs": {
        "pitch": {"target": "RX", "invert": False, "deadzone": 3.0, "max": 20.0, "expo": 1.6},
        "roll": {"target": "RY", "invert": False, "deadzone": 3.0, "max": 20.0, "expo": 1.6},
        "twist": {"target": "RZ", "invert": False, "deadzone": 4.0, "max": 25.0, "expo": 1.6},
    },
    "speed": 1.0,
    "pause_angle": 50.0,
    "auto_recenter_twist": True,
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
