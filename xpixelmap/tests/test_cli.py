"""CLI end to end against the emulated camera (flash kept in a temp file between commands)."""
import contextlib
import io
import unittest

import common
from common import HAVE_UNICORN, BACKUP, PIXEL
from xpixelmap import cli
from xpixelmap.constants import BACKUP_SHA256
from xpixelmap.image import load_image


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = cli.main(['--data-dir', str(common.TMP / 'cli')] + list(argv))
    return rc, out.getvalue()


class CliOffline(unittest.TestCase):
    def test_build_refuses_overwrite_and_verify_offline(self):
        out = common.TMP / 'cli_build.bin'
        rc, txt = run('build', '--out', str(out), '--pixel', f'{PIXEL[0]},{PIXEL[1]}')
        self.assertEqual(rc, 0, txt)
        self.assertEqual(load_image(out), common.edited())
        rc, txt = run('build', '--out', str(out), '--pixel', f'{PIXEL[0]},{PIXEL[1]}')
        self.assertEqual(rc, 2)                                  # exists -> refused
        rc, txt = run('build', '--out', str(common.BACKUP_PATH), '--pixel', '100,100')
        self.assertEqual(rc, 2)
        rc, txt = run('verify', str(out), '--no-emu', '--pixel', f'{PIXEL[0]},{PIXEL[1]}')
        self.assertEqual(rc, 0, txt)
        self.assertIn('ALL PASSED', txt)

    def test_build_with_fuji_partner(self):
        # 4 pixels + the left partner of (4346,2269) in list2; same bytes as helper/edit_lists.py produces
        px = ['2497,1861', '2496,1859', '4346,2269', '2317,1649']
        out = common.TMP / 'cli_build_partner.bin'
        argv = ['build', '--out', str(out)] + [a for p in px for a in ('--pixel', p)] + ['--partner', '4346,2269']
        rc, txt = run(*argv)
        self.assertEqual(rc, 0, txt)
        self.assertIn('added list2 B: raw34=', txt)
        from xpixelmap.build import _edit_lists
        el = _edit_lists()
        pix = [tuple(int(t) for t in p.split(',')) for p in px]
        want, _ = el.add_pixels(load_image(common.BACKUP_PATH), pix, {'list2': [(4345, 2269)]})
        self.assertEqual(load_image(out), want)
        vargv = ['verify', str(out), '--no-emu'] + [a for p in px for a in ('--pixel', p)]
        rc, txt = run(*(vargv + ['--partner', '4346,2269']))
        self.assertEqual(rc, 0, txt)
        rc, txt = run(*vargv)                                     # without --partner the extra record is flagged
        self.assertEqual(rc, 1, txt)
        rc, txt = run('build', '--out', str(common.TMP / 'cli_stray.bin'), '--pixel', '2497,1861', '--partner', '100,100')
        self.assertEqual(rc, 2, txt)                              # partner must be one of the pixels

    def test_camera_commands_need_explicit_transport(self):
        for argv in (('backup',), ('write', str(common.BACKUP_PATH)), ('restore',)):
            rc, txt = run(*argv)
            self.assertEqual(rc, 2, argv)
            self.assertIn('--emu', txt)
        self.assertEqual(common.REAL_INSTANTIATIONS, [])

    def test_save_needs_a_terminal(self):
        self.assertFalse(cli.interactive_confirm('SAVE x TO CAMERA'))   # stdin is not a tty in tests

    def test_confirm_flag_must_match_the_image(self):
        args = cli.parser().parse_args(['save', '--real', '--confirm', 'ABCDEF012345', 'img.bin'])
        confirm = cli._confirm_for(args)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(confirm('SAVE abcdef012345 TO CAMERA'))
            self.assertFalse(confirm('SAVE 000000000000 TO CAMERA'))


@unittest.skipUnless(HAVE_UNICORN, 'needs unicorn (burner container)')
class CliEmulated(unittest.TestCase):
    def test_verify_build_save_backup_restore(self):
        img = common.TMP / 'cli_img.bin'
        img.write_bytes(common.edited())
        state = str(common.TMP / 'emu_flash.bin')
        rc, txt = run('verify', str(img))
        self.assertEqual(rc, 0, txt)
        self.assertIn('emulated round trip: PASS', txt)
        rc, txt = run('save', '--emu', '--emu-flash-state', state, str(img))     # not a tty -> save refused
        self.assertEqual(rc, 0, txt)
        self.assertIn('NOT saved', txt)
        rc, txt = run('save', '--emu', '--yes-emulated', '--emu-flash-state', state, str(img))
        self.assertEqual(rc, 0, txt)
        rc, txt = run('backup', '--emu', '--emu-flash-state', state, '--expect', str(img))
        self.assertEqual(rc, 0, txt)
        self.assertIn('MATCH', txt)
        rc, txt = run('status')
        self.assertIn('STATE: nothing_sent', txt)               # last session was the read-only backup
        rc, txt = run('restore', '--emu', '--yes-emulated', '--emu-flash-state', state)
        self.assertEqual(rc, 0, txt)
        rc, txt = run('backup', '--emu', '--emu-flash-state', state)
        self.assertEqual(rc, 0, txt)
        self.assertIn(f'known backup ({BACKUP_SHA256[:12]}): MATCH', txt)


if __name__ == '__main__':
    unittest.main()
