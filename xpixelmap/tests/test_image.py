import unittest

import common
from common import BACKUP, edited
from xpixelmap.image import presave_checks, load_image, ImageError, list_layout
from xpixelmap.constants import LISTS


def corrupt(img, off, val):
    b = bytearray(img); b[off] = val; return bytes(b)


def set_u24(img, off, v):
    b = bytearray(img); b[off:off + 3] = v.to_bytes(3, 'little'); return bytes(b)


class ImageChecks(unittest.TestCase):
    def test_backup_and_edit_pass(self):
        self.assertEqual(presave_checks(BACKUP, BACKUP), [])
        self.assertEqual(presave_checks(edited(), BACKUP), [])

    def test_raw_correction_gate_only_goes_to_always_on(self):
        self.assertEqual(presave_checks(corrupt(edited(), 0x2802, 127), BACKUP), [])
        self.assertTrue(presave_checks(corrupt(edited(), 0x2802, 60), BACKUP))    # no other value
        self.assertTrue(presave_checks(corrupt(edited(), 0x2806, 0), BACKUP))     # nothing past the gate range

    def test_corruptions_fail(self):
        e = edited()
        cases = {
            'magic': corrupt(e, 0x202, 0x00),
            '0x244': corrupt(e, 0x244, 1),
            '0xf7': corrupt(e, 0xf7, 1),
            'outside lists': corrupt(e, 0x200000, e[0x200000] ^ 0xff),
            'trigger 0x80': corrupt(e, 0x80, 0x00),
            'trigger 0x83': corrupt(e, 0x83, 0x01),
            'app80 area 0x90': corrupt(e, 0x90, 0x01),
            'lens block': corrupt(e, 0x3e0100, e[0x3e0100] ^ 1),
            'B start 0x2906': corrupt(e, 0x2906, e[0x2906] ^ 1),
            'list0 padding bit': corrupt(e, 0x76800 + 0x443c - 1, e[0x76800 + 0x443c - 1] | 0x80),
            'list filler after span': corrupt(e, 0x11c840 + 0x1860e + 2, 0x00),
        }
        for name, img in cases.items():
            self.assertNotEqual(presave_checks(img, BACKUP), [], name)

    def test_corrupted_count_fields(self):
        e = edited()
        for name, base, ca, cb in LISTS:
            for field in (ca, cb):
                for v in (0xffffff, 0x5217c):
                    img = set_u24(e, field, v)
                    bad = presave_checks(img, BACKUP)
                    self.assertNotEqual(bad, [], (name, hex(field), hex(v)))
                    if v == 0xffffff:
                        self.assertTrue(any('limit' in b for b in bad), (name, hex(field), bad))
        # a plausible-looking count off by one (no record behind it) must also fail
        for field, delta in ((0x28e0, 1), (0x28e6, 1), (0x28e3, 1), (0x28e0, -1), (0x28f2, 1)):
            img = set_u24(e, field, int.from_bytes(e[field:field + 3], 'little') + delta)
            self.assertNotEqual(presave_checks(img, BACKUP), [], (hex(field), delta))

    def test_all_six_lists_in_layout(self):
        self.assertEqual(len(list_layout(BACKUP)), 6)
        limits = {n: lim for n, b, A, B, s, lim in list_layout(BACKUP)}
        self.assertEqual(limits, {'list0': 0x11c840, 'list2': 0x2bb8e0, 'list4': 0x3b4940, 'list1': 0x3b8ba8,
                                  'list3': 0x3c31b0, 'list5': 0x400000})

    def test_truncated_file(self):
        p = common.TMP / 'trunc.bin'
        p.write_bytes(BACKUP[:-1])
        with self.assertRaises(ImageError):
            load_image(p)
        p2 = common.TMP / 'long.bin'
        p2.write_bytes(BACKUP + b'\0')
        with self.assertRaises(ImageError):
            load_image(p2)


if __name__ == '__main__':
    unittest.main()
