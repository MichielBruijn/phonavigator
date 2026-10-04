"""Output backends: how the six axes reach the 3D application.

One backend per platform; the rest of the app is platform independent.
"""

import sys


class BackendError(RuntimeError):
    pass


class Backend:
    name = "?"

    def write(self, values):
        """values: six ints (TX TY TZ RX RY RZ) within ±350, ~60 times per second."""
        raise NotImplementedError

    def close(self):
        pass


def create() -> Backend:
    if sys.platform.startswith("linux"):
        from .linux_uinput import UinputBackend

        return UinputBackend()
    raise BackendError(f"No output backend for {sys.platform} yet")
