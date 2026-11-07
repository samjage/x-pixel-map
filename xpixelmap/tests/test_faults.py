"""Every injected fault must end with NO save, or with a successful restore to the backup."""
import unittest

import common
from common import HAVE_UNICORN, BACKUP, edited, journal, quiet, yes
from xpixelmap.session import Session, SessionError, Refused, Aborted, SaveOutcomeUnknown, run_write, run_restore
from xpixelmap.journal import read_events, interpret
from xpixelmap.image import sha256

if HAVE_UNICORN:
    from xpixelmap.emulated import EmulatedCamera, EmulatedTransport, Fault

NCHUNK = 0x420000 // 0x100


def read_camera(t, base=BACKUP):
    s = Session(t, journal('check'), base, log=quiet)
    s.identify()
    return s.backup()[0]


@unittest.skipUnless(HAVE_UNICORN, 'needs unicorn (burner container)')
class Faults(unittest.TestCase):
    def setUp(self):
        self.cam = EmulatedCamera(BACKUP)
        self.t = EmulatedTransport(self.cam)

    def assert_nothing_saved(self, j=None):
        self.assertEqual(self.cam.save_count, 0)
        self.assertEqual(self.cam.flash_image(), BACKUP)
        if j is not None:
            self.assertIn(interpret(read_events(j.dir))[0], ('ram_dirty', 'ram_verified', 'refused', 'nothing_sent'))
        self.cam.reboot()                                     # power-cycle discards RAM
        self.assertEqual(read_camera(self.t), BACKUP)

    def restore_and_check(self, allow_trigger_diff=True):
        s = run_restore(self.t, journal('restore'), BACKUP, confirm=yes, allow_trigger_diff=allow_trigger_diff,
                        log=quiet)
        self.assertTrue(s.saved)
        self.cam.reboot()
        self.assertEqual(read_camera(self.t), BACKUP)

    # ---- USB errors / timeouts / interrupts during write and read-back
    def test_usb_error_mid_write(self):
        for kind, nth in (('fail', 1), ('fail', 5000), ('timeout', 100), ('fail', NCHUNK + 1)):
            with self.subTest(kind=kind, nth=nth):
                self.setUp()
                self.cam.faults = [Fault(kind, 'write', nth)]
                j = journal('usb')
                with self.assertRaises(Aborted):
                    run_write(self.t, j, BACKUP, edited(), confirm=yes, log=quiet)
                self.assertEqual(interpret(read_events(j.dir))[0], 'ram_dirty')
                self.assert_nothing_saved(j)

    def test_interrupt_mid_write(self):
        self.cam.faults = [Fault('interrupt', 'write', 8000)]
        j = journal('ctrlc')
        with self.assertRaises(KeyboardInterrupt):
            run_write(self.t, j, BACKUP, edited(), confirm=yes, log=quiet)
        key, text = interpret(read_events(j.dir))
        self.assertEqual(key, 'ram_dirty')
        self.assertIn('NOTHING WAS SAVED', text)
        self.assert_nothing_saved(j)

    def test_errors_during_readback(self):
        for kind in ('fail', 'timeout'):
            with self.subTest(kind=kind):
                self.setUp()
                s = Session(self.t, journal('rb'), BACKUP, log=quiet)
                s.identify(); s.backup(); s.write_image(edited())
                self.cam.faults = [Fault(kind, 'read', 777)]
                with self.assertRaises(Aborted):
                    s.verify_ram(edited())
                with self.assertRaises(Refused):
                    s.save(edited(), yes)
                self.assert_nothing_saved()

    # ---- corrupted transfers
    def test_corrupted_write_transfer(self):
        for nth in (1, 300, 2000, NCHUNK):
            with self.subTest(nth=nth):
                self.setUp()
                self.cam.faults = [Fault('flip', 'write', nth, byte=7)]
                j = journal('flipw')
                with self.assertRaises(Aborted):
                    run_write(self.t, j, BACKUP, edited(), confirm=yes, log=quiet)
                self.assertEqual(read_events(j.dir)[-1]['event'], 'readback_mismatch')
                self.assert_nothing_saved(j)

    def test_corrupted_read_transfer_during_readback(self):
        s = Session(self.t, journal('flipr'), BACKUP, log=quiet)
        s.identify(); s.backup(); s.write_image(edited())
        self.cam.faults = [Fault('flip', 'read', 500, byte=3)]
        with self.assertRaises(Aborted):
            s.verify_ram(edited())
        self.assert_nothing_saved()

    def test_corrupted_read_during_backup_blocks_write(self):
        self.cam.faults = [Fault('flip', 'read', 1234)]
        with self.assertRaises(Refused):
            run_write(self.t, journal('flipb'), BACKUP, edited(), confirm=yes, log=quiet)
        self.assertEqual(self.cam.calls.get('write', 0), 0)
        self.assert_nothing_saved()

    # ---- power loss during save (several block counts), then restore
    def test_power_loss_during_save(self):
        for k in (0, 1, 2, 5, 16, 32, 33):
            with self.subTest(blocks=k):
                self.setUp()
                self.cam.faults = [Fault('power_loss', 'save', 1, blocks=k)]
                j = journal(f'pl{k}')
                with self.assertRaises(SaveOutcomeUnknown):
                    run_write(self.t, j, BACKUP, edited(), confirm=yes, log=quiet)
                key, text = interpret(read_events(j.dir))
                self.assertEqual(key, 'save_unknown')
                with self.assertRaises(Exception):
                    self.t.info()                             # camera is off until reboot
                self.cam.reboot()
                now = read_camera(self.t)
                if k == 0: self.assertEqual(now, BACKUP)
                if k == 33: self.assertEqual(now, edited())
                self.restore_and_check()

    def test_power_loss_during_restore_save_D1r(self):
        run_write(self.t, journal('edit'), BACKUP, edited(), confirm=yes, log=quiet)
        self.cam.reboot()
        self.assertEqual(read_camera(self.t, edited()), edited())
        for k in (0, 1, 5, 32):
            with self.subTest(blocks=k):
                self.cam.faults = [Fault('power_loss', 'save', 1, blocks=k)]
                j = journal(f'rpl{k}')
                with self.assertRaises(SaveOutcomeUnknown):
                    run_restore(self.t, j, BACKUP, confirm=yes, allow_trigger_diff=True, log=quiet)
                self.assertEqual(interpret(read_events(j.dir))[0], 'save_unknown')
                self.cam.faults = []
                self.cam.reboot()
                self.restore_and_check()                      # a second restore repairs it
                # put the edited image back for the next block count
                run_write(self.t, journal('edit'), BACKUP, edited(), confirm=yes, log=quiet)
                self.cam.reboot()

    def test_flash_write_error_reported(self):
        # the 0x22 handler passes the flash result to the status writer FUN_010dccd0: response 22 01
        self.cam.faults = [Fault('ftl_error', 'save', 1)]
        j = journal('ftlerr')
        with self.assertRaises(SessionError):
            run_write(self.t, j, BACKUP, edited(), confirm=yes, log=quiet)
        ev = read_events(j.dir)
        self.assertEqual(ev[-1]['event'], 'save_error')
        self.assertEqual(ev[-1]['response'][:4], '2201')
        self.assertEqual(interpret(ev)[0], 'save_error')
        self.cam.reboot()
        self.assertEqual(read_camera(self.t), BACKUP)        # nothing reached flash (stubbed FTL error)
        self.restore_and_check()

    # ---- bad inputs / wrong camera
    def test_corrupted_image_file_refused(self):
        e = edited()
        bad_imgs = {
            'magic': e[:0x202] + b'\0\0' + e[0x204:],
            'count list0 A + 1': e[:0x28e0] + (3073).to_bytes(3, 'little') + e[0x28e3:],
            'count list5 A huge': e[:0x28fe] + (0x5217c).to_bytes(3, 'little') + e[0x2901:],
            'count list2 B huge': e[:0x28e9] + (0xffffff).to_bytes(3, 'little') + e[0x28ec:],
            'byte outside lists': e[:0x300000] + bytes([e[0x300000] ^ 0x40]) + e[0x300001:],
        }
        for name, img in bad_imgs.items():
            with self.subTest(name=name):
                self.setUp()
                j = journal('badimg')
                with self.assertRaises(Refused):
                    run_write(self.t, j, BACKUP, img, confirm=yes, log=quiet)
                self.assertEqual(self.cam.calls.get('write', 0), 0)
                self.assertEqual(interpret(read_events(j.dir))[0], 'refused')
                self.assert_nothing_saved()

    def test_wrong_camera_or_backup_mismatch(self):
        other = BACKUP[:0x11b] + b'Z' + BACKUP[0x11c:]       # different serial number
        self.cam = EmulatedCamera(other); self.t = EmulatedTransport(self.cam)
        with self.assertRaises(Refused):
            run_write(self.t, journal('wrongcam'), BACKUP, edited(), confirm=yes, log=quiet)
        self.assertEqual(self.cam.calls.get('write', 0), 0)
        self.assertEqual(self.cam.flash_image(), other)
        self.cam = EmulatedCamera(BACKUP, product='X-T10'); self.t = EmulatedTransport(self.cam)
        with self.assertRaises(Refused):
            run_write(self.t, journal('wrongmodel'), BACKUP, edited(), confirm=yes, log=quiet)
        self.assertEqual(self.cam.calls.get('read', 0), 0)

    def test_restore_from_factory_defaults(self):
        # bad marker in flash -> boot falls back to the factory copy (lists empty, 0x80..0x83 = 00 00 00 00)
        broken = BACKUP[:0x202] + b'\0\0' + BACKUP[0x204:]
        self.cam = EmulatedCamera(broken); self.t = EmulatedTransport(self.cam)
        ram = self.cam.ram()
        self.assertEqual(ram[0x202:0x204], b'\x55\xaa')
        self.assertEqual(ram[0x28e0:0x2904], bytes(0x24))     # all list counts zero
        self.assertTrue(self.cam.raw_reads)                   # the factory-copy path ran
        if ram[0x80:0x84] != BACKUP[0x80:0x84]:
            with self.assertRaises(Refused):                  # strict mode refuses differing trigger bytes
                run_restore(self.t, journal('def1'), BACKUP, confirm=yes, log=quiet)
            self.cam.reboot()
        self.restore_and_check(allow_trigger_diff=True)      # app80 save rewrites 22 ff 00 00


if __name__ == '__main__':
    unittest.main()
