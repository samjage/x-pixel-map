import ast
import sys
import unittest
from pathlib import Path

import common
from common import HAVE_UNICORN, BACKUP
from xpixelmap.allowlist import check_write, check_read, ForbiddenOperation
from xpixelmap.transport import Transport, ALLOWED_OPS
from xpixelmap import cli


class AllowlistTest(unittest.TestCase):
    def test_trigger_bytes_refused(self):
        for off, n in ((0x80, 1), (0x81, 1), (0x83, 1), (0x7f, 2), (0x00, 0x100), (0x83, 0x10), (0x80, 33)):
            with self.assertRaises(ForbiddenOperation, msg=(hex(off), n)):
                check_write(off, b'\0' * n)
        check_write(0x0, b'\0' * 0x80)       # ends right before 0x80
        check_write(0x84, b'\0' * 0x7c)      # starts right after 0x83

    def test_range_and_size_refused(self):
        for off, n in ((-1, 1), (0x41ffff, 2), (0x420000, 1), (0x41ff01, 0x100), (0, 0), (0x1000, 0x101)):
            with self.assertRaises(ForbiddenOperation, msg=(off, n)):
                check_write(off, b'\0' * n)
        check_write(0x41ffff, b'\0')
        check_write(0x41ff00, b'\0' * 0x100)
        for off, n in ((-1, 1), (0x41ff01, 0x100), (0, 0), (0, 0x101), (0x420000, 1)):
            with self.assertRaises(ForbiddenOperation):
                check_read(off, n)
        check_read(0x80, 33)                 # reading the app80 response is allowed
        with self.assertRaises(ForbiddenOperation):
            check_write(True, b'\0')
        with self.assertRaises(ForbiddenOperation):
            check_write(0x100, 'text')

    def test_no_extra_public_ops(self):
        with self.assertRaises(TypeError):
            class Bad(Transport):
                def ram_read(self, a): pass
        with self.assertRaises(TypeError):
            class Bad2(Transport):
                def write(self, off, data): pass
        with self.assertRaises(TypeError):
            class Bad3(Transport):
                syslog = None
        from xpixelmap.real import RealTransport
        self.assertEqual({n for n in dir(RealTransport) if not n.startswith('_')}, set(ALLOWED_OPS))

    def test_real_module_uses_only_allowed_jig_calls(self):
        src = (Path(common.__file__).resolve().parents[1] / 'real.py').read_text()
        used = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) \
                    and node.value.attr in ('_jig', '_ftl'):
                used.add(node.attr)
        allowed = {'info_tags', 'get_device_info', 'get_config_data_size', 'read_config_data', 'write_config_data',
                   'app80_request', 'app80_response', 'open_session', 'close_session', 'nop'}
        self.assertTrue(used <= allowed, used - allowed)
        for forbidden in ('debug_', 'firmware_upload', 'set_config_usb_debug', 'syslog', 'dump_ram', '0x660002',
                          '0x200001'):
            self.assertNotIn(forbidden, src.split('"""', 2)[-1])

    def test_real_transport_needs_real_flag(self):
        args = cli.parser().parse_args(['backup'])
        with self.assertRaises(cli.ToolError):
            cli.make_transport(args)                      # neither --emu nor --real
        args = cli.parser().parse_args(['backup', '--emu', '--real'])
        with self.assertRaises(cli.ToolError):
            cli.make_transport(args)
        self.assertEqual(common.REAL_INSTANTIATIONS, [])
        self.assertNotIn('usb1', sys.modules)

    @unittest.skipUnless(HAVE_UNICORN, 'needs unicorn')
    def test_emulated_transport_refuses_before_sending(self):
        from xpixelmap.emulated import EmulatedCamera, EmulatedTransport
        cam = EmulatedCamera(BACKUP)
        t = EmulatedTransport(cam)
        self.assertEqual({n for n in dir(t) if not n.startswith('_')}, set(ALLOWED_OPS))
        for off, data in ((0x80, b'\x22'), (0x7f, b'\0\0'), (0x41ffff, b'\0\0'), (0, b'\0' * 0x101)):
            with self.assertRaises(ForbiddenOperation):
                t.write(off, data)
        self.assertEqual(cam.calls.get('write', 0), 0)    # nothing reached the camera
        self.assertEqual(cam.ram(), BACKUP)
        t.write(0x84, BACKUP[0x84:0x90])
        self.assertEqual(cam.calls['write'], 1)


if __name__ == '__main__':
    unittest.main()
