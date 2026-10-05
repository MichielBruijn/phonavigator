import copy
import math
import unittest

from phonavigator import config
from phonavigator.mapper import FULL_SCALE, Mapper
from phonavigator.tap import TapDetector


def qaxis(axis, deg):
    h = math.radians(deg) / 2
    s = math.sin(h)
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(h))


def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


# Upright: device Y = world up.
UPRIGHT = qaxis((1, 0, 0), 90)


def pose(pitch=0.0, roll=0.0, yaw=0.0, base_yaw=0.0):
    q = qmul(qaxis((0, 0, 1), base_yaw + yaw), UPRIGHT)
    q = qmul(q, qaxis((1, 0, 0), pitch))
    return qmul(q, qaxis((0, 0, 1), roll))


class MapperTest(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(config.DEFAULTS)
        self.cfg["auto_recenter_twist"] = False
        self.cfg["speed"] = 1.0
        # Independent of the shipped defaults: one plain axis per movement.
        for name, target in (("pitch", "RX"), ("roll", "RY"), ("twist", "RZ")):
            self.cfg["inputs"][name].update(target=target, invert=False, deadzone=3.0, max=20.0)
        self.m = Mapper(self.cfg)
        self.t = 0.0

    def step(self, q, n=1, covered=False):
        for _ in range(n):
            self.t += 0.016
            self.m.feed(q, self.t, covered)
            v = self.m.compute(self.t, 0.016)
        return dict(zip(("TX", "TY", "TZ", "RX", "RY", "RZ"), v))

    def test_rest_is_zero(self):
        out = self.step(pose(base_yaw=37), 5)
        self.assertEqual(self.m.state, "active")
        self.assertTrue(all(v == 0 for v in out.values()))

    def test_axes_independent(self):
        self.step(pose(base_yaw=10))
        out = self.step(pose(pitch=15, base_yaw=10))
        self.assertNotEqual(out["RX"], 0)
        self.assertEqual((out["RY"], out["RZ"]), (0, 0))
        out = self.step(pose(roll=15, base_yaw=10))
        self.assertNotEqual(out["RY"], 0)
        self.assertEqual((out["RX"], out["RZ"]), (0, 0))
        out = self.step(pose(yaw=15, base_yaw=10))
        self.assertNotEqual(out["RZ"], 0)
        self.assertEqual((out["RX"], out["RY"]), (0, 0))

    def test_sign_convention(self):
        # pose(pitch=+) tilts the top towards the screen side; pose(yaw=+) is counter-clockwise from above.
        self.step(pose())
        self.step(pose(pitch=10))
        self.assertGreater(self.m.angles["pitch"], 0)
        self.step(pose(yaw=10))
        self.assertLess(self.m.angles["twist"], 0)

    def test_full_scale_and_sign(self):
        self.step(pose())
        self.assertEqual(self.step(pose(pitch=30))["RX"], -self.step(pose(pitch=-30))["RX"])
        self.assertEqual(abs(self.step(pose(pitch=30))["RX"]), FULL_SCALE)

    def test_invert_and_target(self):
        self.step(pose())
        a = self.step(pose(yaw=15))["RZ"]
        self.cfg["inputs"]["twist"]["invert"] = True
        self.cfg["inputs"]["twist"]["target"] = "TX"
        out = self.step(pose(yaw=15))
        self.assertEqual(out["TX"], -a)
        self.assertEqual(out["RZ"], 0)

    def test_lying_down_pauses_and_resets_twist(self):
        self.step(pose())
        self.step(pose(pitch=-90))  # flat on its back
        self.assertEqual(self.m.state, "flat")
        # Picked up facing 60° elsewhere: that direction is the new zero.
        out = self.step(pose(base_yaw=60))
        self.assertEqual(self.m.state, "active")
        self.assertEqual(out["RZ"], 0)

    def test_covered_pauses_and_resets_twist(self):
        self.step(pose())
        out = self.step(pose(pitch=15, yaw=30), covered=True)  # in a pocket, moving
        self.assertEqual(self.m.state, "covered")
        self.assertTrue(all(v == 0 for v in out.values()))
        out = self.step(pose(base_yaw=45))  # out again, facing elsewhere: new twist zero
        self.assertEqual(self.m.state, "active")
        self.assertEqual(out["RZ"], 0)

    def test_charging_port_up_feels_the_same(self):
        # Upside down = extra 180° about the screen normal, applied in the device frame.
        def upside_down(**kw):
            return qmul(pose(**kw), qaxis((0, 0, 1), 180))

        normal = {}
        self.step(pose(base_yaw=20))
        for kw in ({"pitch": 15}, {"roll": 15}, {"yaw": 15}):
            normal[str(kw)] = self.step(pose(base_yaw=20, **kw))
        for mode in ("auto", "up"):
            self.setUp()
            self.cfg["charging_port"] = mode
            self.step(upside_down(base_yaw=20))
            self.assertEqual(self.m.state, "active")
            for kw in ({"pitch": 15}, {"roll": 15}, {"yaw": 15}):
                self.assertEqual(self.step(upside_down(base_yaw=20, **kw)), normal[str(kw)], (mode, kw))

    def test_charging_port_down_fixed(self):
        self.cfg["charging_port"] = "down"
        self.step(qmul(pose(), qaxis((0, 0, 1), 180)))
        self.assertEqual(self.m.state, "flat")

    def test_calibration(self):
        self.step(pose(pitch=10))
        self.assertTrue(self.m.calibrate())
        self.assertEqual(self.step(pose(pitch=10))["RX"], 0)

    def test_no_data(self):
        self.step(pose())
        self.t += 1.0
        self.assertEqual(self.m.compute(self.t, 0.016), [0] * 6)
        self.assertEqual(self.m.state, "nodata")

    def test_drift_correction(self):
        self.cfg["auto_recenter_twist"] = True
        self.step(pose())
        self.step(pose(yaw=2), 600)  # 2° drift, inside the 3° deadzone
        self.assertLess(abs(self.m.angles["twist"]), 0.1)


class TapTest(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(config.DEFAULTS)
        self.cfg["tap_threshold"] = 15.0
        self.taps = TapDetector(self.cfg)

    def test_double_tap(self):
        self.cfg["tap_calibrate"] = "double"
        self.taps.feed(30, 10.0)
        self.taps.feed(30, 10.02)  # same knock still ringing
        self.assertFalse(self.taps.due(11.0))
        self.taps.feed(30, 10.3)
        self.assertFalse(self.taps.due(10.4))
        self.assertTrue(self.taps.due(10.7))
        self.assertFalse(self.taps.due(10.8))  # only once

    def test_taps_too_far_apart(self):
        self.cfg["tap_calibrate"] = "double"
        self.taps.feed(30, 10.0)
        self.taps.feed(30, 11.0)
        self.assertFalse(self.taps.due(12.0))

    def test_single_tap_and_threshold(self):
        self.cfg["tap_calibrate"] = "single"
        self.taps.feed(10, 10.0)  # below threshold: hand movement
        self.assertFalse(self.taps.due(11.0))
        self.taps.feed(20, 12.0)
        self.assertTrue(self.taps.due(12.3))
        self.assertGreater(self.taps.mute_until, 12.0)

    def test_off(self):
        self.cfg["tap_calibrate"] = "off"
        self.taps.feed(50, 10.0)
        self.taps.feed(50, 10.3)
        self.assertFalse(self.taps.due(11.0))


if __name__ == "__main__":
    unittest.main()
