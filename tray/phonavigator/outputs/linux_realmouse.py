"""Real 3Dconnexion devices, merged into the virtual one.

spacenavd 1.3 sends clients the motion of its newest device only, so a real SpaceMouse and
Phonavigator cannot drive it at the same time. The tray therefore reads the real devices
itself and adds them to its own output; the virtual device is recreated after a real one is
plugged in, so it stays the newest. Real devices are grabbed so spacenavd does not see them
twice; when spacenavd holds the grab already (grab = true in spnavrc, the default), they are
read through hidraw instead.
"""

import os
import struct

from evdev import InputDevice, list_devices
from evdev import ecodes as e

_ABS = (e.ABS_X, e.ABS_Y, e.ABS_Z, e.ABS_RX, e.ABS_RY, e.ABS_RZ)
_REL = (e.REL_X, e.REL_Y, e.REL_Z, e.REL_RX, e.REL_RY, e.REL_RZ)
OWN_PHYS = "py-evdev-uinput"


def is_real(dev) -> bool:
    if dev.phys == OWN_PHYS:
        return False
    vid, pid = dev.info.vendor, dev.info.product
    if not (vid == 0x256F or (vid == 0x046D and 0xC603 <= pid <= 0xC62B)):
        return False
    caps = dev.capabilities()
    # Rotation axes: skips CadMice and keyboards with the same vendor id.
    return (e.ABS_RX in [c for c, _ in caps.get(e.EV_ABS, [])]
            or e.REL_RX in caps.get(e.EV_REL, []))


def _hidraw_path(dev):
    sysdir = f"/sys/class/input/{os.path.basename(dev.path)}/device/device/hidraw"
    try:
        return "/dev/" + sorted(os.listdir(sysdir))[0]
    except (OSError, IndexError):
        return None


class RealDevice:

    def __init__(self, dev):
        self.dev = dev
        self.name = dev.name
        caps = dev.capabilities()
        self.keys = sorted(caps.get(e.EV_KEY, []))
        self.range = max((ai.max for c, ai in caps.get(e.EV_ABS, []) if c in _ABS), default=0)
        self.axes = [0] * 6
        self.pressed = set()
        self.hid = None
        self.error = None
        try:
            dev.grab()
        except OSError:
            path = _hidraw_path(dev)
            try:
                self.hid = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except (OSError, TypeError) as ex:
                self.error = f"grabbed by spacenavd and no access to {path} ({ex})"

    def read(self) -> bool:
        """Take in pending input; False once the device is gone."""
        try:
            if self.hid is not None:
                while True:
                    try:
                        self.report(os.read(self.hid, 64))
                    except BlockingIOError:
                        break
            elif self.error is None:
                while (ev := self.dev.read_one()) is not None:
                    self._event(ev)
        except OSError:
            return False
        return True

    def _event(self, ev):
        if ev.type == e.EV_ABS and ev.code in _ABS:
            self.axes[_ABS.index(ev.code)] = ev.value
        elif ev.type == e.EV_REL and ev.code in _REL:
            self.axes[_REL.index(ev.code)] = ev.value
        elif ev.type == e.EV_KEY:
            (self.pressed.add if ev.value else self.pressed.discard)(ev.code)

    def report(self, data: bytes):
        """One HID input report: 1 = translation (or all six axes), 2 = rotation, 3 = buttons."""
        if not data:
            return
        rid = data[0]
        if rid == 1 and len(data) >= 13:
            self.axes = list(struct.unpack_from("<6h", data, 1))
        elif rid in (1, 2) and len(data) >= 7:
            base = 0 if rid == 1 else 3
            self.axes[base:base + 3] = struct.unpack_from("<3h", data, 1)
        elif rid == 3:
            bits = int.from_bytes(data[1:], "little")
            self.pressed = {code for i, code in enumerate(self.keys) if bits >> i & 1}

    def close(self):
        if self.hid is not None:
            os.close(self.hid)
        self.dev.close()  # also releases the grab


class RealDevices:

    def __init__(self):
        self.devices = {}  # event path -> RealDevice
        self._other = set()  # paths checked and not ours to read

    def scan(self) -> bool:
        """Pick up plugged and unplugged devices; True when a real one was added."""
        paths = set(list_devices())  # only the ones we may open
        for p in [p for p in self.devices if p not in paths]:
            self.devices.pop(p).close()
        self._other &= paths
        added = False
        for p in sorted(paths - self.devices.keys() - self._other):
            try:
                dev = InputDevice(p)
            except OSError:
                continue  # permissions may still be on their way; try next scan
            if is_real(dev):
                self.devices[p] = RealDevice(dev)
                added = True
            else:
                dev.close()
                self._other.add(p)
        return added

    def read(self):
        """Summed axes and the union of pressed buttons of all real devices."""
        axes, pressed = [0] * 6, set()
        for p, d in list(self.devices.items()):
            if not d.read():
                self.devices.pop(p).close()
                continue
            axes = [a + b for a, b in zip(axes, d.axes)]
            pressed |= d.pressed
        return axes, pressed

    def keys(self):
        return {k for d in self.devices.values() for k in d.keys}

    def range(self):
        return max((d.range for d in self.devices.values()), default=0)

    def describe(self):
        return ", ".join(f"{d.name} ({d.error})" if d.error else d.name
                         for d in self.devices.values())

    def close(self):
        for d in self.devices.values():
            d.close()
        self.devices.clear()
