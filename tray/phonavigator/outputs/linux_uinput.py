"""Virtual 3Dconnexion SpaceMouse via uinput; spacenavd picks it up by itself.

Poses as a SpaceMouse Compact (256f:c635), so spacenavd applies the same axis
convention as for a real one and /etc/spnavrc keeps meaning the same.
"""

from evdev import AbsInfo, UInput, UInputError
from evdev import ecodes as e

from . import Backend, BackendError

_CODES = (e.ABS_X, e.ABS_Y, e.ABS_Z, e.ABS_RX, e.ABS_RY, e.ABS_RZ)
_FULL = 350


class UinputBackend(Backend):
    name = "spacenavd (uinput)"

    def __init__(self):
        caps = {
            e.EV_ABS: [(c, AbsInfo(0, -_FULL, _FULL, 0, 0, 0)) for c in _CODES],
            e.EV_KEY: [e.BTN_0, e.BTN_1],
        }
        try:
            self.ui = UInput(caps, name="Phonavigator", vendor=0x256F, product=0xC635,
                             version=1, bustype=e.BUS_USB)
        except (OSError, UInputError) as ex:
            raise BackendError(f"No access to /dev/uinput ({ex}); see install-linux.sh") from ex
        self.last = [0] * 6

    def write(self, values):
        changed = False
        for i, (code, v) in enumerate(zip(_CODES, values)):
            # The kernel drops ABS events that repeat the previous value and spacenavd does
            # not repeat by default: a held deflection has to change a little every tick.
            if v != 0 and v == self.last[i]:
                v += -1 if v > 1 else 1
            if v != self.last[i]:
                self.ui.write(e.EV_ABS, code, v)
                self.last[i] = v
                changed = True
        if changed:
            self.ui.syn()

    def close(self):
        self.write([0] * 6)
        self.ui.close()
