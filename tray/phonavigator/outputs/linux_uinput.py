"""Virtual 3Dconnexion SpaceMouse via uinput; spacenavd picks it up by itself.

Poses as a SpaceMouse Compact (256f:c635), so spacenavd applies the same axis
convention as for a real one. Axis options in /etc/spnavrc are undone (see spnavrc.py),
so the tray settings behave the same on every machine. Real SpaceMice are merged in
(see linux_realmouse.py).
"""

from evdev import AbsInfo, UInput, UInputError
from evdev import ecodes as e

from . import Backend, BackendError, linux_realmouse, spnavrc

_CODES = (e.ABS_X, e.ABS_Y, e.ABS_Z, e.ABS_RX, e.ABS_RY, e.ABS_RZ)
_FULL = 350
SCAN_EVERY = 60  # ticks
RECREATE_AFTER = 90  # ticks; spacenavd adds a new device 1 s after hotplug, ours must come later


class UinputBackend(Backend):

    def __init__(self):
        self.ui = None
        self.real = linux_realmouse.RealDevices()
        self.real.scan()
        self._create()
        self.spnavrc = spnavrc.Watcher()
        self._ticks = 0
        self._recreate_at = 0

    def _create(self):
        """(Re)create the virtual device, with the buttons and range of the real ones."""
        self.keys = {e.BTN_0, e.BTN_1} | self.real.keys()
        self.full = max(_FULL, self.real.range())
        caps = {
            e.EV_ABS: [(c, AbsInfo(0, -self.full, self.full, 0, 0, 0)) for c in _CODES],
            e.EV_KEY: sorted(self.keys),
        }
        if self.ui:
            self.ui.close()
        try:
            self.ui = UInput(caps, name="Phonavigator", vendor=0x256F, product=0xC635,
                             version=1, bustype=e.BUS_USB, phys=linux_realmouse.OWN_PHYS)
        except (OSError, UInputError) as ex:
            self.real.close()
            raise BackendError(f"No access to /dev/uinput ({ex}); see install-linux.sh") from ex
        self.last = [0] * 6
        self.pressed = set()

    @property
    def name(self):
        name = "spacenavd (uinput)"
        if self.spnavrc.fn:
            name += ", undoing /etc/spnavrc axes"
        if self.real.devices:
            name += ", merging " + self.real.describe()
        return name

    def write(self, values):
        self._ticks += 1
        if self._ticks % SCAN_EVERY == 0:
            self.spnavrc.check()
            if self.real.scan():
                self._recreate_at = self._ticks + RECREATE_AFTER
        if self._recreate_at and self._ticks >= self._recreate_at:
            self._recreate_at = 0
            self._create()
        if self.spnavrc.fn:
            values = self.spnavrc.fn(values)
        # Real devices go out as they are: spnavrc is meant for them.
        real, pressed = self.real.read()
        values = [max(-self.full, min(self.full, v + r)) for v, r in zip(values, real)]
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
        pressed &= self.keys  # buttons of a device plugged in after the last _create
        for code in pressed ^ self.pressed:
            self.ui.write(e.EV_KEY, code, int(code in pressed))
            changed = True
        self.pressed = pressed
        if changed:
            self.ui.syn()

    def close(self):
        self.write([0] * 6)
        self.ui.close()
        self.real.close()
