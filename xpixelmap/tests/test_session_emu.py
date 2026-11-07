"""Happy path and ordering rules against the emulated camera (real firmware code in unicorn)."""
import unittest

import common
from common import HAVE_UNICORN, BACKUP, edited, journal, quiet, yes
from xpixelmap.session import Session, Refused, run_write, run_restore
from xpixelmap.journal import read_events, interpret
from xpixelmap.constants import IMAGE_SIZE, TRIGGER, SAVE_PHRASE
from xpixelmap.image import sha256
from xpixelmap.transport import Transport

if HAVE_UNICORN:
    from xpixelmap.emulated import EmulatedCamera, EmulatedTransport, per_shot_counts


def fresh(flash=None):
    cam = EmulatedCamera(BACKUP if flash is None else flash)
    return cam, EmulatedTransport(cam)


def read_camera(t, base=BACKUP):
    s = Session(t, journal('check'), base, log=quiet)
    s.identify()
    return s.backup()[0]


@unittest.skipUnless(HAVE_UNICORN, 'needs unicorn (burner container)')
class HappyPath(unittest.TestCase):
    def test_edit_write_verify_save_reboot_restore(self):
        img = edited()
        cam, t = fresh()
        phrases = []

        def typed(phrase):
            phrases.append(phrase)
            return phrase
        j = journal('happy')
        s = run_write(t, j, BACKUP, img, confirm=lambda p: typed(p) == SAVE_PHRASE.format(sha12=sha256(img)[:12]),
                      backup_dir=common.TMP / 'backups', log=quiet)
        self.assertTrue(s.saved)
        self.assertEqual(phrases, [f'SAVE {sha256(img)[:12]} TO CAMERA'])
        self.assertEqual(cam.save_count, 1)
        self.assertEqual(interpret(read_events(j.dir))[0], 'saved')
        self.assertEqual(cam.flash_image(), img)
        cam.reboot()                                           # battery out/in, power on
        self.assertEqual(read_camera(t, img), img)             # backup == intended
        counts = per_shot_counts(cam.ram())
        # PIXEL is an AF (phase-detect) pixel, so it goes into list1 + list3, used by the mode-3 pass
        self.assertIn((3, 0x3b4940, 33), counts['1/180 s'])    # list1 count used per shot (32 + 1)
        self.assertIn((3, 0x3b8ba8, 223), counts['4 s'])       # list3 count used per shot (222 + 1)
        self.assertIn((1, 0x76800, 3071), counts['1/180 s'])   # main lists unchanged
        self.assertIn((1, 0x11c840, 17503), counts['4 s'])
        r = run_restore(t, journal('restore'), BACKUP, confirm=yes, log=quiet)
        self.assertTrue(r.saved)
        cam.reboot()
        self.assertEqual(read_camera(t), BACKUP)
        from xpixelmap.constants import BACKUP_SHA256
        self.assertEqual(sha256(read_camera(t)), BACKUP_SHA256)

    def test_backup_files_never_overwritten(self):
        cam, t = fresh()
        s = Session(t, journal('bk'), BACKUP, log=quiet); s.identify()
        _, sha, p1 = s.backup(common.TMP / 'bk')
        _, _, p2 = s.backup(common.TMP / 'bk2')
        self.assertNotEqual(p1, None)
        with self.assertRaises(FileExistsError):
            s.backup(common.TMP / 'bk')                        # same session, same name -> refuses


@unittest.skipUnless(HAVE_UNICORN, 'needs unicorn (burner container)')
class Ordering(unittest.TestCase):
    def test_chunk_layout(self):
        cam, t = fresh()
        offs = []

        class Rec(Transport):
            def _info(self): return t._info()
            def _config_size(self): return t._config_size()
            def _read(self, o, n): return t._read(o, n)
            def _write(self, o, d): offs.append((o, len(d))); return t._write(o, d)
            def _save(self): return t._save()
        s = Session(Rec(), journal('layout'), BACKUP, log=quiet)
        s.identify(); s.backup(); s.write_image(edited()); s.verify_ram(edited())
        self.assertEqual(len(offs), IMAGE_SIZE // 0x100 + 1)
        self.assertEqual(sum(n for _, n in offs), IMAGE_SIZE - 4)
        self.assertFalse(any(o < TRIGGER[1] and o + n > TRIGGER[0] for o, n in offs))
        self.assertEqual(offs[:2], [(0, 0x80), (0x84, 0x7c)])
        self.assertTrue(all(n <= 0x100 for _, n in offs))

    def test_save_requires_write_and_verify(self):
        img = edited()
        cam, t = fresh()
        s = Session(t, journal('order1'), BACKUP, log=quiet)
        s.identify(); s.backup()
        with self.assertRaises(Refused):
            s.save(img, yes)                                   # nothing written
        s2 = Session(t, journal('order2'), BACKUP, log=quiet)
        with self.assertRaises(Refused):
            s2.write_image(img)                                # no identify/backup
        s3 = Session(t, journal('order3'), BACKUP, log=quiet)
        s3.identify(); s3.backup(); s3.write_image(img)
        with self.assertRaises(Refused):
            s3.save(img, yes)                                  # written but not verified
        with self.assertRaises(Refused):
            s3.write_image(img)                                # one write per session
        self.assertEqual(cam.save_count, 0)
        self.assertEqual(cam.flash_image(), BACKUP)

    def test_wrong_phrase_declines(self):
        img = edited()
        cam, t = fresh()
        j = journal('decline')
        s = run_write(t, j, BACKUP, img, confirm=lambda p: False, log=quiet)
        self.assertFalse(s.saved)
        self.assertEqual(cam.save_count, 0)
        self.assertEqual(interpret(read_events(j.dir))[0], 'ram_verified')
        cam.reboot()
        self.assertEqual(read_camera(t), BACKUP)               # RAM edit discarded by power cycle

    def test_image_changed_on_disk_before_save(self):
        img = edited()
        p = common.TMP / 'img_changes.bin'
        p.write_bytes(img)
        cam, t = fresh()
        s = Session(t, journal('disk'), BACKUP, log=quiet)
        s.identify(); s.backup(); s.write_image(img); s.verify_ram(img)
        p.write_bytes(BACKUP)                                  # file swapped after verification
        with self.assertRaises(Refused):
            s.save(img, yes, image_path=p)
        self.assertEqual(cam.save_count, 0)

    def test_save_is_the_real_app80_sequence(self):
        img = edited()
        cam, t = fresh()
        run_write(t, journal('app80'), BACKUP, img, confirm=yes, log=quiet)
        self.assertTrue(any(m[1] == 0x1707 for m in cam.messages))   # trigger posted the app80 message
        self.assertEqual(cam.flash_image()[0x80:0xa1], bytes([0x22, 0xff]) + bytes(31))
        self.assertEqual(cam.ram(0x80, 4), bytes([0x22, 0, 0, 0]))   # status 0 written after the save


if __name__ == '__main__':
    unittest.main()
