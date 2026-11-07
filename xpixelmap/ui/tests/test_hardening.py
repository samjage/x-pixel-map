"""Hardening tests: input validation, timeouts and the read-back check (no camera needed)."""
import shutil, sys, tempfile, unittest
import numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine as EN, camera as K      # noqa: E402


import atexit as _atexit, shutil as _shutil, tempfile as _tempfile


def _mk():
    """A scratch directory that is removed when the test run ends (sessions of a demo run are large)."""
    d = _tempfile.mkdtemp(); _atexit.register(_shutil.rmtree, d, True); return d

def one_step_session(kind='instruct', items=None):
    import tempfile
    return EN.Session(Path(_mk()) / 's', [EN.Step('a', 'P', 'A', kind, items=items or [{'text': 'x'}])], lambda: 'normal')


class StaleFrame(unittest.TestCase):
    def test_capture_clears_the_old_frame_first(self):
        src = (Path(K.__file__)).read_text()
        i = src.index("C.gp('--capture-image-and-download'")
        self.assertIn('old.unlink()', src[i - 400:i])        # the unlink sits right before the capture call


class ShutterCheck(unittest.TestCase):
    def test_electronic_dark_frames_are_refused(self):
        src = (Path(K.__file__)).read_text()
        self.assertIn("if kind == 'dark' and est == 'ES': raise StopRequest(REASONS['shutter'])", src)
        self.assertIn('Mechanical', K.REASONS['shutter'])

    def test_checklist_no_longer_asks_for_electronic(self):
        import recipe as R
        texts = [i['text'] for r in R.RECIPES.values() for st in r() for i in st.items]
        self.assertFalse([t for t in texts if 'Electronic' in t and 'Mechanical' not in t])


class GuidePage(unittest.TestCase):
    def test_how_it_works_page_is_wired_in(self):
        ui = Path(K.__file__).resolve().parent
        html = (ui / 'static' / 'index.html').read_text()
        js = (ui / 'static' / 'app.js').read_text()
        self.assertIn('<section id="about"', html)
        for anchor in ('about-what', 'about-camera', 'about-changes', 'about-session',
                       'about-focus', 'about-limits', 'about-tech', 'about-words'):
            self.assertIn(f'id="{anchor}"', html)
        self.assertIn("about: $('about')", js)
        self.assertIn('function openAbout()', js)
        self.assertIn('function closeAbout()', js)
        self.assertIn("e.key === 'Escape' && page === 'about'", js)


class SoftwareIso(unittest.TestCase):
    def test_the_computer_sets_iso_before_every_set(self):
        src = (Path(K.__file__)).read_text()
        self.assertLess(src.index("X.set_exposure(iso, seconds)"), src.index("C.auto_exposure(out, name"))
        self.assertLess(src.index("expose(t)"), src.index("C.gp('--capture-image-and-download'"))

    def test_a_real_build_needs_the_firmware(self):
        src = (Path(K.__file__).parent / 'sort.py').read_text()
        self.assertLess(src.index("not firmware.ready()"), src.index("Path(base).read_bytes(); orig = d"))

    def test_read_back_names_its_base(self):      # installs have no factory dump, so the CLI default would not exist
        self.assertIn("'backup', '--real', '--base', camera_image(sess)[0]", Path(K.__file__).read_text())

    def test_a_white_frame_that_went_dark_stops_the_run(self):
        src = (Path(K.__file__)).read_text()
        self.assertIn("REASONS['white']", src); self.assertLess(src.index("REASONS['white'].format"), src.index("files.append(str(dst))"))

    def test_no_step_asks_for_a_numbered_iso_before_the_end(self):
        import recipe as R
        texts = [i['text'] for st in R.RECIPES['map']() if st.id != 'end_200' for i in st.items]
        self.assertFalse([t for t in texts if 'Turn the ISO dial' in t])
        self.assertTrue([t for t in texts if 'ISO dial on A' in t])
        self.assertFalse([t for t in texts if 'on T' in t])


class LauncherIcon(unittest.TestCase):
    def test_the_header_logo_is_the_launcher_icon(self):
        ui = Path(K.__file__).parent
        self.assertEqual((ui / 'static' / 'x-pixel-map.svg').read_bytes(), (ui.parents[1] / 'packaging' / 'linux' / 'x-pixel-map.svg').read_bytes())
        self.assertIn('src="/static/x-pixel-map.svg"', (ui / 'static' / 'index.html').read_text())


class ShellTimeout(unittest.TestCase):
    def slow(self): return [sys.executable, '-c', 'import time; time.sleep(5)']

    def test_timeout_is_a_clear_stop(self):
        with self.assertRaises(EN.StopRequest) as cm: K.sh(self.slow(), 1)
        self.assertIn('did not finish within 1 s', str(cm.exception)); self.assertNotIn('battery', str(cm.exception))

    def test_write_timeout_says_what_to_do(self):
        with self.assertRaises(EN.StopRequest) as cm: K.sh(self.slow(), 1, writes=True)
        self.assertIn('half way through a write', str(cm.exception)); self.assertIn('battery', str(cm.exception))


class ReadBackVerdict(unittest.TestCase):
    def test_only_the_expected_image_line_counts(self):
        ok = '  known backup (abc123): differs\n  expected image /x/cfg.bin: MATCH\n'
        self.assertTrue(K.read_back_matches(ok))
        self.assertFalse(K.read_back_matches('  known backup (abc123): MATCH\n  expected image /x/cfg.bin: DIFFERS\n'))
        self.assertFalse(K.read_back_matches('  known backup (abc123): MATCH\n'))       # verdict line missing
        self.assertFalse(K.read_back_matches(''))


class ActInput(unittest.TestCase):
    def test_tick_rejects_bad_items(self):
        s = one_step_session()
        for bad in (-1, 1, 'x', None): self.assertFalse(s.act('tick', item=bad)['ok'], bad)
        self.assertFalse(s.act('tick')['ok'])
        self.assertTrue(s.act('tick', item=0)['ok'])

    def test_review_accepts_only_xy_strings(self):
        s = one_step_session('review')
        for bad in ('1,2', [1, 2], ['1;2'], ['1,2,3'], [['1', '2']], None): self.assertFalse(s.act('review', selected=bad)['ok'], bad)
        self.assertTrue(s.act('review', selected=['1,2', '300,40'])['ok'])




class HistoryArchive(unittest.TestCase):
    def setUp(self):
        import archive
        self.A = archive
        self.d = Path(_mk())
        self.addCleanup(shutil.rmtree, self.d, True)
        fr = self.d / 'frames'; fr.mkdir()
        self.raw = fr / 'dark_01.RAF'; self.raw.write_bytes(b'raw')
        self.npy = fr / 'dark_01.minexcess.npy'
        m = np.zeros((30, 40), np.int32); m[5, 7] = 500; m[9, 9] = 99999     # the second is clipped, never wraps
        np.save(self.npy, m)
        self.sets = {'dark': {'kind': 'dark', 'files': [str(self.raw)]}}

    def test_archive_keeps_the_excess_and_loads_it_back(self):
        self.assertEqual(self.A.save(self.d, self.sets), ['dark'])
        a = self.A.load_mean(self.d, 'dark', False, 1024)
        self.assertEqual(a[5, 7], 1024 + 500); self.assertEqual(a[9, 9], 1024 + self.A.HI)

    def test_frames_are_deleted_only_when_archived(self):
        self.assertEqual(self.A.clear_frames(self.d, self.sets, ['dark']), 0)     # no archive file yet: nothing deleted
        self.assertTrue(self.raw.exists() and self.npy.exists())
        self.A.save(self.d, self.sets)
        self.assertGreater(self.A.clear_frames(self.d, self.sets, ['dark']), 0)
        self.assertFalse(self.raw.exists() or self.npy.exists())
        self.assertIsNotNone(self.A.load_mean(self.d, 'dark', False, 1024))      # the history copy survives

    def test_simulation_and_keep_flag_never_clear(self):
        import sort as SO
        class S:                                   # just what _compress_and_clear touches
            simulate = True; dir = self.d; ctx = {'sets': self.sets}
            def log(self, *a, **k): pass
        SO._compress_and_clear(S, lambda *a, **k: None)
        self.assertTrue(self.raw.exists())
