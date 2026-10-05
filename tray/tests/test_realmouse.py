import struct
import unittest

from evdev import AbsInfo
from evdev import ecodes as e

from phonavigator.outputs import linux_realmouse as rm


class FakeDev:
    def __init__(self, vendor=0x046D, product=0xC626, phys="usb-1", grab_fails=False, rel=False):
        self.info = type("I", (), {"vendor": vendor, "product": product})()
        self.phys, self.name, self.path = phys, "SpaceNavigator", "/dev/input/event99"
        self.grab_fails = grab_fails
        axes = [e.REL_X, e.REL_Y, e.REL_Z, e.REL_RX, e.REL_RY, e.REL_RZ]
        self.caps = ({e.EV_REL: axes} if rel else
                     {e.EV_ABS: [(c, AbsInfo(0, -500, 500, 0, 0, 0)) for c in rm._ABS]})
        self.caps[e.EV_KEY] = [e.BTN_1, e.BTN_0]
        self.events = []

    def capabilities(self):
        return self.caps

    def grab(self):
        if self.grab_fails:
            raise OSError(16, "busy")

    def read_one(self):
        return self.events.pop(0) if self.events else None

    def close(self):
        pass


class Ev:
    def __init__(self, type_, code, value):
        self.type, self.code, self.value = type_, code, value


class RealMouseTest(unittest.TestCase):

    def test_matching(self):
        self.assertTrue(rm.is_real(FakeDev()))
        self.assertTrue(rm.is_real(FakeDev(0x256F, 0xC635, rel=True)))
        self.assertFalse(rm.is_real(FakeDev(phys=rm.OWN_PHYS)))  # ourselves
        self.assertFalse(rm.is_real(FakeDev(0x046D, 0xC52B)))  # Logitech receiver
        cadmouse = FakeDev(0x256F, 0xC650)
        cadmouse.caps = {e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL]}
        self.assertFalse(rm.is_real(cadmouse))

    def test_evdev(self):
        dev = FakeDev()
        d = rm.RealDevice(dev)
        self.assertEqual((d.range, d.keys), (500, [e.BTN_0, e.BTN_1]))
        dev.events = [Ev(e.EV_ABS, e.ABS_RY, -120), Ev(e.EV_ABS, e.ABS_X, 7),
                      Ev(e.EV_KEY, e.BTN_1, 1)]
        self.assertTrue(d.read())
        self.assertEqual(d.axes, [7, 0, 0, 0, -120, 0])
        self.assertEqual(d.pressed, {e.BTN_1})

    def test_hid_reports(self):
        d = rm.RealDevice(FakeDev())
        d.report(bytes([1]) + struct.pack("<3h", 10, -20, 30))
        d.report(bytes([2]) + struct.pack("<3h", -1, 2, -3))
        self.assertEqual(d.axes, [10, -20, 30, -1, 2, -3])
        d.report(bytes([1]) + struct.pack("<6h", 1, 2, 3, 4, 5, 6))  # newer devices: one report
        self.assertEqual(d.axes, [1, 2, 3, 4, 5, 6])
        d.report(bytes([3, 0b10, 0]))
        self.assertEqual(d.pressed, {e.BTN_1})

    def test_grabbed_without_hidraw(self):
        d = rm.RealDevice(FakeDev(grab_fails=True))  # no sysfs for event99
        self.assertIsNotNone(d.error)
        self.assertTrue(d.read())
        self.assertEqual(d.axes, [0] * 6)

    def test_sum(self):
        devs = rm.RealDevices()
        a, b = rm.RealDevice(FakeDev()), rm.RealDevice(FakeDev())
        a.axes, b.axes = [1, 0, 0, 0, 5, 0], [0, 0, 2, 0, -1, 0]
        b.pressed = {e.BTN_0}
        devs.devices = {"a": a, "b": b}
        self.assertEqual(devs.read(), ([1, 0, 2, 0, 4, 0], {e.BTN_0}))


if __name__ == "__main__":
    unittest.main()
