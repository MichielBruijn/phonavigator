import itertools
import unittest

from phonavigator.outputs import spnavrc

LENOVO = """
sensitivity = 1.5
dead-zone = 2
invert-rot = y
invert-trans = y
swap-yz = true
"""


def spacenavd(text, raw):
    """What spacenavd 1.3 hands to applications for raw input values (signs/axes only)."""
    out = [0] * 6
    for i, (axis, sign) in enumerate(spnavrc._chain(spnavrc.parse(text))):
        out[axis] += sign * raw[i]
    return out


VALUES = [11, 22, 33, 44, 55, 66]


class SpnavrcTest(unittest.TestCase):
    def check(self, text):
        fn = spnavrc.compensation(spnavrc.parse(text))
        sent = fn(VALUES) if fn else VALUES
        self.assertEqual(spacenavd(text, sent), spacenavd("", VALUES), text)

    def test_default_needs_nothing(self):
        self.assertIsNone(spnavrc.compensation(spnavrc.parse("# nothing\nled = on\n")))

    def test_lenovo(self):
        self.check(LENOVO)

    def test_all_combinations(self):
        for swap in (False, True):
            for rot in ("", "x", "y", "z", "xyz"):
                for trans in ("", "y", "xz"):
                    self.check(f"swap-yz = {'true' if swap else 'false'}\n"
                               f"invert-rot = {rot}\ninvert-trans = {trans}\n")

    def test_axismap_permutation(self):
        for perm in itertools.islice(itertools.permutations(range(6)), 0, 720, 37):
            self.check("".join(f"axismap{i} = {a}\n" for i, a in enumerate(perm)) + "swap-yz = true\n")

    def test_axismap_not_a_permutation(self):
        self.assertIsNone(spnavrc.compensation(spnavrc.parse("axismap1 = 0\n")))


if __name__ == "__main__":
    unittest.main()
