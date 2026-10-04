"""Uitvoer-backends: hoe de zes assen bij de 3D-applicatie komen.

Per platform een eigen backend; de rest van de app is platformonafhankelijk.
"""

import sys


class BackendError(RuntimeError):
    pass


class Backend:
    name = "?"

    def write(self, values):
        """values: zes ints (TX TY TZ RX RY RZ) in ±350, ~60x per seconde."""
        raise NotImplementedError

    def close(self):
        pass


def create() -> Backend:
    if sys.platform.startswith("linux"):
        from .linux_uinput import UinputBackend

        return UinputBackend()
    raise BackendError(f"Nog geen uitvoer-backend voor {sys.platform}")
