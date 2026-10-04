import copy
import math
import unittest

from phonavigator import config
from phonavigator.mapper import FULL_SCALE, Mapper


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


# Rechtop, scherm naar de gebruiker: device-Y = wereld-omhoog.
UPRIGHT = qaxis((1, 0, 0), 90)


def pose(pitch=0.0, roll=0.0, yaw=0.0, base_yaw=0.0):
    q = qmul(qaxis((0, 0, 1), base_yaw + yaw), UPRIGHT)
    q = qmul(q, qaxis((1, 0, 0), pitch))
    return qmul(q, qaxis((0, 0, 1), roll))


class MapperTest(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(config.DEFAULTS)
        self.cfg["auto_recenter_twist"] = False
        self.m = Mapper(self.cfg)
        self.t = 0.0

    def step(self, q, n=1):
        for _ in range(n):
            self.t += 0.016
            self.m.feed(q, self.t)
            v = self.m.compute(self.t, 0.016)
        return dict(zip(("TX", "TY", "TZ", "RX", "RY", "RZ"), v))

    def test_rust_is_nul(self):
        out = self.step(pose(base_yaw=37), 5)
        self.assertEqual(self.m.state, "active")
        self.assertTrue(all(v == 0 for v in out.values()))

    def test_assen_gescheiden(self):
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

    def test_vol_gas_en_teken(self):
        self.step(pose())
        self.assertEqual(self.step(pose(pitch=30))["RX"], -self.step(pose(pitch=-30))["RX"])
        self.assertEqual(abs(self.step(pose(pitch=30))["RX"]), FULL_SCALE)

    def test_omdraaien_en_doelas(self):
        self.step(pose())
        a = self.step(pose(yaw=15))["RZ"]
        self.cfg["inputs"]["twist"]["invert"] = True
        self.cfg["inputs"]["twist"]["target"] = "TX"
        out = self.step(pose(yaw=15))
        self.assertEqual(out["TX"], -a)
        self.assertEqual(out["RZ"], 0)

    def test_neerleggen_pauzeert_en_reset_twist(self):
        self.step(pose())
        self.step(pose(pitch=-90))  # plat op de rug
        self.assertEqual(self.m.state, "flat")
        # Opgepakt met 60° andere richting: die richting is de nieuwe nul.
        out = self.step(pose(base_yaw=60))
        self.assertEqual(self.m.state, "active")
        self.assertEqual(out["RZ"], 0)

    def test_kalibratie(self):
        self.step(pose(pitch=10))
        self.assertTrue(self.m.calibrate())
        self.assertEqual(self.step(pose(pitch=10))["RX"], 0)

    def test_geen_data(self):
        self.step(pose())
        self.t += 1.0
        self.assertEqual(self.m.compute(self.t, 0.016), [0] * 6)
        self.assertEqual(self.m.state, "nodata")

    def test_drift_correctie(self):
        self.cfg["auto_recenter_twist"] = True
        self.step(pose())
        self.step(pose(yaw=3), 600)  # 3° drift, binnen deadzone van 4°
        self.assertLess(abs(self.m.angles["twist"]), 0.1)


if __name__ == "__main__":
    unittest.main()
