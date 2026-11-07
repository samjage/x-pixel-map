import unittest

from xpixelmap import firmware as F


class Firmware(unittest.TestCase):
    def stream(self, body, unpacked):
        import struct
        return struct.pack('<5I', unpacked, 20 + len(body), 1, 0x4000, 4) + body

    def test_literals_and_a_match(self):
        self.assertEqual(F.decompress(self.stream(bytes([3]) + b'abc' + bytes([0x80, 0x33]), 6)), b'abcabc')

    def test_wrong_size_is_refused(self):
        with self.assertRaises(F.FirmwareError): F.decompress(self.stream(bytes([1]) + b'a', 2))

    def test_any_other_file_is_refused(self):
        with self.assertRaises(F.FirmwareError): F.install(b'not the firmware')


if __name__ == '__main__':
    unittest.main()
