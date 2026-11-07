import sys, unittest, tempfile, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine as EN       # noqa: E402

import atexit as _atexit, shutil as _shutil, tempfile as _tempfile


def _mk():
    """A scratch directory that is removed when the test run ends (sessions of a demo run are large)."""
    d = _tempfile.mkdtemp(); _atexit.register(_shutil.rmtree, d, True); return d

class ResumeVersionCheckTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(_mk())
    def tearDown(self):
        shutil.rmtree(self.root)
    def make_steps(self, n):
        return [EN.Step(f'id{i}', 'P', f'Tit{i}', 'instruct', items=[{'text': str(i)}]) for i in range(n)]
    def test_refuses_step_id_mismatch(self):
        # Save a session with 3 steps
        steps3 = self.make_steps(3)
        s = EN.Session(self.root/'s', steps3, lambda: 'normal')
        s.state['steps'][0]['status'] = 'done'; s.save()
        # Now try to resume with 4 steps (recipe changed)
        steps4 = self.make_steps(4)
        with self.assertRaises(ValueError) as cm:
            EN.Session(self.root/'s', steps4, lambda: 'normal')
        self.assertIn('different version of the recipe', str(cm.exception))
    def test_refuses_corrupt_state(self):
        steps = self.make_steps(2)
        p = self.root/'s'; p.mkdir()
        (p/'state.json').write_text('notjson')
        with self.assertRaises(ValueError):
            EN.Session(p, steps, lambda: 'normal')

if __name__ == '__main__':
    unittest.main()
