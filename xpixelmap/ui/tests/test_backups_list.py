"""Roll back must offer every image in the backups folder, whatever the file is called.

The folder holds three kinds of file for the same image ('-backup_', '-save_', '-restore_'), plus anything
copied in by hand. Listing only '*-backup_*' hid images that were written but never read back, and a dump
copied in from an older date was invisible.
"""
import os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import camera as K      # noqa: E402

SIZE = K.IMAGE_SIZE


def put(root, name, fill, when):
    """A config-image-sized file with the mtime it would have had, so 'newest first' is testable."""
    p = Path(root) / name
    p.write_bytes(bytes([fill]) * SIZE)
    os.utime(p, (when, when))
    return p


class EveryImageIsOffered(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(self.d, True))

    def list_in(self, root):
        old = K.BACKUPS
        K.BACKUPS = root
        try: return K.backups_list(None)
        finally: K.BACKUPS = old

    def test_saved_restored_and_copied_in_images_all_appear(self):
        put(self.d, 'cfgdata_20261106T181038Z-backup_aaaa11112222.bin', 1, 1000)
        put(self.d, 'cfgdata_20261106T180911Z-save_bbbb33334444.bin', 2, 900)
        put(self.d, 'cfgdata_20261107T021113Z-restore_aaaa11112222.bin', 1, 1100)
        put(self.d, 'fuji-service-2025-09-23.bin', 3, 500)
        rows = self.list_in(self.d)
        got = [r['sha12'] for r in rows]
        self.assertEqual(3, len(rows), 'three images: aaaa (backup+restore), bbbb (save), the copied-in dump')
        self.assertIn('bbbb33334444', got, 'a save that was never read back is offered')
        self.assertIn(K.sha12(self.d / 'fuji-service-2025-09-23.bin'), got, 'a hand-copied dump is offered')
        self.assertEqual(rows[0]['path'], str(self.d / 'cfgdata_20261106T181038Z-backup_aaaa11112222.bin'),
                         'newest image first, shown as its read-back')

    def test_the_same_image_is_listed_once_and_the_read_back_is_shown(self):
        put(self.d, 'cfgdata_20261106T180911Z-save_aaaa11112222.bin', 1, 900)
        put(self.d, 'cfgdata_20261106T181038Z-backup_aaaa11112222.bin', 1, 1000)
        put(self.d, 'cfgdata_20261107T021113Z-restore_aaaa11112222.bin', 1, 1100)
        rows = self.list_in(self.d)
        self.assertEqual(1, len(rows))
        self.assertEqual('backup', rows[0]['kind'], 'the read-back is the locked proof, so it is the entry shown')

    def test_a_saved_image_is_never_called_original(self):
        put(self.d, 'cfgdata_20261106T180911Z-save_bbbb33334444.bin', 2, 900)
        put(self.d, 'cfgdata_20261106T181038Z-backup_aaaa11112222.bin', 1, 1000)
        by_sha = {r['sha12']: r['name'] for r in self.list_in(self.d)}
        self.assertEqual('Original', by_sha['aaaa11112222'], 'the oldest read-back is the original')
        self.assertTrue(by_sha['bbbb33334444'].startswith('Saved'), 'a written image says so')

    def test_a_file_that_is_not_an_image_is_ignored(self):
        put(self.d, 'cfgdata_20261106T181038Z-backup_aaaa11112222.bin', 1, 1000)
        (self.d / 'notes.bin').write_bytes(b'not an image')
        (self.d / 'cfgdata_truncated.bin').write_bytes(b'\0' * 1024)
        rows = self.list_in(self.d)
        self.assertEqual(1, len(rows), 'only full-size config images are offered')

    def test_has_image_sees_saves_too(self):
        put(self.d, 'cfgdata_20261106T180911Z-save_bbbb33334444.bin', 2, 900)
        old = K.BACKUPS
        K.BACKUPS = self.d
        try: self.assertTrue(K.has_image(), 'Roll back must be offered when only a save is present')
        finally: K.BACKUPS = old


if __name__ == '__main__':
    unittest.main()
