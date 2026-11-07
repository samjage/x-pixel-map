"""Tests for the session UI: engine rules, sorting rules, the candidate builder and a full simulated run.
Run: <python with numpy + pillow> -m unittest discover -s xpixelmap/ui/tests -v   (no camera, no network)
"""
import json, shutil, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np

UI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI)); sys.path.insert(0, str(UI.parents[1]))
import engine as EN, camera as K, sort as SO, recipe as R      # noqa: E402

V12 = [K.SIM_AFTER] if K.SIM_AFTER.is_file() else []        # demo fixtures are not included; those tests skip without them


import atexit as _atexit, shutil as _shutil, tempfile as _tempfile


def _mk():
    """A scratch directory that is removed when the test run ends (sessions of a demo run are large)."""
    d = _tempfile.mkdtemp(); _atexit.register(_shutil.rmtree, d, True); return d

def mksession(steps, mode='normal', simulate=False):
    d = Path(_mk())
    box = {'mode': mode}
    s = EN.Session(d / 's', steps, lambda: box['mode'], simulate)
    return s, box


class Engine(unittest.TestCase):
    def test_timers_and_camera_gate(self):
        st = [EN.Step('a', 'P', 'A', 'instruct', items=[{'text': 'x'}, {'text': 'wait', 'wait': 60}], wait_for='service')]
        s, box = mksession(st)
        self.assertFalse(s.act('continue')['ok'])                 # nothing ticked
        s.act('tick', item=0); s.act('tick', item=1)
        self.assertIn('timers', s.act('continue')['error'])       # timer still running
        s.state['steps'][0]['ticks']['1'] = time.time() - 1       # timer ran out
        self.assertIn('service', s.act('continue')['error'])      # camera not in service mode
        box['mode'] = 'service'
        self.assertTrue(s.act('continue')['ok'])

    def test_simulation_skips_timers(self):
        st = [EN.Step('a', 'P', 'A', 'instruct', items=[{'text': 'wait', 'wait': 60}])]
        s, _ = mksession(st, simulate=True)
        s.act('tick', item=0)
        self.assertTrue(s.act('continue')['ok'])

    def test_approve_needs_matching_confirm_and_skip(self):
        ran = []
        st = [EN.Step('w', 'P', 'Write', 'approve', fn=lambda s, p: ran.append(1) or {},
                      summary=lambda s: {'title': 't', 'lines': [], 'confirm': 'abc123abc123'}, writes=True),
              EN.Step('x', 'P', 'Skipped', 'instruct', items=[{'text': 'x'}], skip_if=lambda s: True),
              EN.Step('y', 'P', 'Last', 'instruct', items=[{'text': 'y'}])]
        s, _ = mksession(st)
        self.assertFalse(s.act('approve', confirm='wrong')['ok']); self.assertEqual(ran, [])
        self.assertTrue(s.act('approve', confirm='abc123abc123')['ok'])
        for _ in range(50):
            if s.state['index'] == 2: break
            time.sleep(0.05)
        self.assertEqual(ran, [1]); self.assertEqual(s.state['steps'][1]['status'], 'skipped'); self.assertEqual(s.state['index'], 2)

    def test_interrupted_run_is_never_assumed_done(self):
        st = [EN.Step('r', 'P', 'Run', 'run', fn=lambda s, p: {})]
        s, _ = mksession(st)
        s.state['steps'][0]['status'] = 'running'; s.save()
        s2 = EN.Session(s.dir, st, lambda: 'normal')
        self.assertEqual(s2.state['steps'][0]['status'], 'pending')


@unittest.skipUnless(V12, 'needs the v12 backup')
class Sorting(unittest.TestCase):
    def fake(self, maps, peaks=None, thr=100):
        s, _ = mksession([EN.Step('x', 'P', 'X', 'instruct')])
        s.ctx['sets'] = {}
        for name, iso, *_ in SO.SCAN:
            m = maps.get(name, np.zeros((3296, 4992), np.int32)); pk = (peaks or {}).get(name, m)
            f = s.dir / f'{name}_01.RAF'; f.write_bytes(b''); np.save(f.with_suffix('.minexcess.npy'), m); np.save(f.with_suffix('.peakexcess.npy'), pk)
            hot = [(int(x), int(y), int(pk[y, x])) for y, x in zip(*np.nonzero((m > thr) | (pk > 200)))]
            s.ctx['sets'][name] = {'kind': 'dark', 'iso': iso, 'thr': thr, 'files': [str(f)], 'hot': hot}
        return s

    def test_rules(self):
        z = lambda: np.zeros((3296, 4992), np.int32)
        a, b, c, short, late = z(), z(), z(), z(), z()              # ISO 200, 320, 400, 800 at 1/200, 1250 at 4 s
        pa, pb = z(), z()
        a[1000, 1000] = 110; b[1000, 1000] = 120                    # every frame in two sets: hot
        a[1100, 1100] = 130                                         # every frame in one set up to ISO 1000: hot
        late[1200, 1500] = 130                                      # every frame, but only warm at ISO 1250: left alone
        pa[1300, 1600] = 480; pb[1300, 1600] = 420                  # about 0, single-frame spikes in two sets: listed
        pa[1400, 1700] = 480                                        # one spike in one set: left alone
        for m in (a, b, c): m[1649, 2317] = 600                     # focus grid site
        short[2000, 3000] = 300; a[2000, 2999] = 40                 # pair: short hot, left neighbour leaks
        a[500, 4935] = 400                                          # frame edge
        late[1789, 3208] = 130                                      # focus lattice, one warm set only: not a focus fix
        names = {'dark_4th_iso200': a, 'dark_4th_iso320': b, 'dark_4th_iso400': c, 'dark_short_iso800': short, 'dark_4s_iso1250': late}
        peaks = {n: np.maximum(m, {'dark_4th_iso200': pa, 'dark_4th_iso320': pb}.get(n, m)) for n, m in names.items()}
        s = self.fake(names, peaks)
        K_img = K.camera_image
        try:
            K.camera_image = SO.camera_image = lambda sess=None: (str(V12[-1]), K.sha12(V12[-1]))
            SO.classify(s, lambda *a, **k: None)
        finally:
            K.camera_image = SO.camera_image = K_img
        got = {(f['x'], f['y']): f['cls'] for f in s.ctx['found']}
        self.assertEqual(got[(1000, 1000)], 'ordinary'); self.assertEqual(got[(1100, 1100)], 'ordinary')
        self.assertEqual(got[(1500, 1200)], 'faint-iso'); self.assertEqual(got[(1600, 1300)], 'spike'); self.assertEqual(got[(1700, 1400)], 'faint-iso')
        self.assertEqual(got[(2317, 1649)], 'focus'); self.assertEqual(got[(3000, 2000)], 'pair'); self.assertEqual(got[(4935, 500)], 'margin'); self.assertEqual(got[(3208, 1789)], 'focus-faint')
        sel = {(f['x'], f['y']): f['selected'] for f in s.ctx['found']}
        self.assertTrue(sel[(1600, 1300)]); self.assertFalse(sel[(1500, 1200)])    # repeating spikes are pre-ticked, a one-off is not
        self.assertTrue(s.ctx['focus_fix_in_camera'])                              # v12 has cfg 0x113b6 = 0

    def test_build_with_nothing_to_change_finishes_cleanly(self):
        s, _ = mksession([EN.Step('x', 'P', 'X', 'instruct')], simulate=True)
        s.ctx['found'] = []; s.ctx['selected'] = []; s.ctx['candidate'] = {'old': 1}
        d = bytearray(Path(V12[-1]).read_bytes()); d[0x2800:0x2805] = bytes([127] * 5)
        f = s.dir / 'cfg_backup_x.bin'; f.write_bytes(d)
        K_img = K.camera_image
        try:
            K.camera_image = SO.camera_image = lambda sess=None: (str(f), 'x')
            self.assertEqual(SO.build(s, lambda *a, **k: None)['changes'], 0)
        finally:
            K.camera_image = SO.camera_image = K_img
        self.assertNotIn('candidate', s.ctx)

    def test_build_adds_pixel_and_verifies(self):
        s, _ = mksession([EN.Step('x', 'P', 'X', 'instruct')], simulate=True)
        s.ctx['found'] = [{'x': 1000, 'y': 1000, 'cls': 'ordinary', 'label': 'Hot pixel'}]
        s.ctx['selected'] = ['1000,1000']
        K_img = K.camera_image
        try:
            K.camera_image = SO.camera_image = lambda sess=None: (str(V12[-1]), K.sha12(V12[-1]))
            SO.build(s, lambda *a, **k: None)
        finally:
            K.camera_image = SO.camera_image = K_img
        c = s.ctx['candidate']
        self.assertEqual(c['changes'], ['(1000,1000)', 'raw correction on at every shutter speed: cfg 0x2800..0x2804 = 127']); self.assertFalse(c['focus_table'])
        self.assertEqual(Path(c['path']).read_bytes()[0x2800:0x2805], bytes([127] * 5))
        Path(c['path']).unlink()


@unittest.skipUnless((K.PROJECT / 'captures' / R.SIM['dark_short_iso800'][0]).exists(), 'needs the saved sessions')
class Simulation(unittest.TestCase):
    def test_full_run(self):
        import server
        old = server.SESSIONS; server.SESSIONS = Path(_mk())
        self.addCleanup(lambda: (shutil.rmtree(server.SESSIONS, ignore_errors=True), setattr(server, 'SESSIONS', old)))   # the run copies ~1 GB of frames
        s = server.open_session(True, True, 'map')
        t0 = time.time()
        while time.time() - t0 < 300:
            v = s.view(); i = v['index']
            if i >= len(v['steps']): break
            st = v['steps'][i]
            self.assertNotEqual(st['status'], 'failed', st['note'])
            if st['kind'] == 'instruct':
                for k, it in enumerate(st['items']):
                    if it['status'] == 'todo': s.act('tick', item=k)
                s.act('continue')
            elif st['kind'] == 'review': s.act('review', selected=[f"{f['x']},{f['y']}" for f in s.ctx['found'] if f['selected']])
            elif st['kind'] == 'approve' and st['status'] == 'pending': s.act('approve', confirm=st['summary']['confirm'])
            time.sleep(0.2)
        rep = s.ctx['report']
        focus = [r for r in rep['rows'] if (r['x'], r['y']) == (2317, 1649)][0]
        self.assertTrue(all(v[2] for k, v in focus.items() if k.startswith('dark_')))   # the focus pixel is fixed after the run
        self.assertTrue(s.ctx['candidate']['focus_table'])                               # simulation starts from v11b: table added


@unittest.skipUnless(K.SIM_START.is_file(), 'needs the demo fixtures (not included)')
class HomeMenu(unittest.TestCase):
    """The home menu talks to the real HTTP handler with a temporary sessions directory."""

    def setUp(self):
        import server
        self.server = server
        self.old_sessions, self.old_session, self.old_sim = server.SESSIONS, server.Handler.session, server.Handler.simulate
        server.SESSIONS = Path(_mk())
        server.Handler.simulate = True
        server.Handler.session = None

        self.map = server.open_session(True, True, 'map')
        self.map.state['index'] = 2; self.map.save()                  # an unfinished pixel-mapping session
        self.roll = server.open_session(True, True, 'rollback')
        self.roll.state['index'] = len(self.roll.steps); self.roll.save()   # a finished rollback session

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.httpd.server_port}'

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join(2)
        shutil.rmtree(self.server.SESSIONS, ignore_errors=True)
        self.server.SESSIONS, self.server.Handler.session, self.server.Handler.simulate = \
            self.old_sessions, self.old_session, self.old_sim

    def request(self, path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(self.base + path, data=body, method='POST' if body else 'GET',
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b'{}')
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b'{}')

    def test_home_state_before_a_session(self):
        code, v = self.request('/api/state')
        self.assertEqual(code, 200)
        self.assertTrue(v['home'])
        self.assertEqual(v['device']['model'], 'X-T1')
        self.assertIn('now', v['holds'])

    def test_sessions_lists_recipe_step_and_finished(self):
        code, d = self.request('/api/sessions')
        self.assertEqual(code, 200)
        self.assertTrue(d['simulate'])
        rows = {x['folder']: x for x in d['sessions']}
        self.assertEqual(len(rows), 2)
        m = rows[self.map.dir.name]
        self.assertEqual((m['recipe'], m['index'], m['total'], m['finished']), ('map', 2, len(self.map.steps), False))
        self.assertEqual(m['step'], 'Into service mode, once')
        r = rows[self.roll.dir.name]
        self.assertEqual((r['recipe'], r['finished']), ('rollback', True))

    def test_resume_unfinished_session(self):
        code, d = self.request('/api/resume', {'folder': self.map.dir.name})
        self.assertEqual(code, 200, d)
        self.assertTrue(d['ok'])
        self.assertEqual(self.server.Handler.session.dir, self.map.dir)
        self.assertEqual(self.server.Handler.session.state['index'], 2)
        self.assertEqual(self.server.Handler.session.recipe, 'map')

    def test_resume_refuses_finished_and_outside_folders(self):
        code, d = self.request('/api/resume', {'folder': self.roll.dir.name})
        self.assertEqual(code, 400)
        self.assertIn('finished', d['error'])
        code, d = self.request('/api/resume', {'folder': '../../etc'})
        self.assertEqual(code, 400)
        self.assertIn('not found', d['error'])

    def test_back_to_home_keeps_the_session_saved(self):
        self.assertEqual(self.request('/api/resume', {'folder': self.map.dir.name})[0], 200)
        code, d = self.request('/api/home', {})
        self.assertEqual((code, d['ok']), (200, True))
        self.assertIsNone(self.server.Handler.session)
        code, d = self.request('/api/sessions')
        row = {x['folder']: x for x in d['sessions']}[self.map.dir.name]
        self.assertFalse(row['finished'])

    def test_history_opens_a_finished_session_read_only(self):
        s = self.server.open_session(True, True, 'map')
        s.state['index'] = len(s.steps); s.state['ctx']['report'] = {'fixed': 8, 'total': 9, 'rows': []}
        s.state['ctx']['candidate'] = {'sha12': 'abc', 'changes': ['a', 'b']}; s.save()
        self.server.Handler.session = None
        code, d = self.request('/api/sessions')
        row = {x['folder']: x for x in d['sessions']}[s.dir.name]
        self.assertTrue(row['finished']); self.assertEqual((row['summary']['fixed'], row['summary']['total'], row['summary']['changes']), (8, 9, 2))
        self.assertEqual(self.request('/api/resume', {'folder': s.dir.name})[0], 400)                    # resume still refuses
        self.assertEqual(self.request('/api/resume', {'folder': s.dir.name, 'view': True})[0], 200)      # history opens it
        code, v = self.request('/api/state')
        self.assertEqual(v['index'], len(s.steps)); self.assertEqual(v['folder'], s.dir.name)
        code, d = self.request('/api/act', {'action': 'continue'})                                       # nothing can be done in it
        self.assertFalse(d.get('ok'))

    def test_history_opens_a_finished_session_from_an_older_recipe(self):
        s = self.server.open_session(True, True, 'map')
        s.state['index'] = len(s.steps) + 1                                        # one step the recipe no longer has
        s.state['steps'].append({'id': 'dropped_step', 'status': 'done', 'ticks': {}, 'note': ''}); s.save()
        self.server.Handler.session = None
        self.assertEqual(self.request('/api/resume', {'folder': s.dir.name})[0], 400)
        self.assertEqual(self.request('/api/resume', {'folder': s.dir.name, 'view': True})[0], 200)
        code, v = self.request('/api/state')
        self.assertEqual(len(v['steps']), len(s.steps) + 1); self.assertEqual(v['steps'][-1]['id'], 'dropped_step')

    def test_clear_history_removes_only_finished_sessions_of_this_mode(self):
        done = self.server.open_session(True, True, 'map')
        done.state['index'] = len(done.steps); done.save()
        open_one = self.server.open_session(True, True, 'map')              # not finished: must survive
        self.server.Handler.session = open_one
        code, d = self.request('/api/sessions')
        self.assertEqual(d['history']['count'], 2)                          # `done` and the finished rollback from setUp
        self.assertEqual(self.request('/api/history/clear', {})[0], 400)    # needs the explicit confirmation
        self.assertTrue(done.dir.exists())
        code, d = self.request('/api/history/clear', {'confirm': True})
        self.assertEqual((code, d['ok'], d['removed']), (200, True, 2))
        self.assertFalse(done.dir.exists()); self.assertTrue(open_one.dir.exists()); self.assertTrue(self.map.dir.exists())
        self.assertEqual(self.request('/api/sessions')[1]['history']['count'], 0)

    def test_clear_history_is_refused_while_a_step_runs(self):
        done = self.server.open_session(True, True, 'map')
        done.state['index'] = len(done.steps); done.save()
        s = self.server.open_session(True, True, 'map')
        i = next(k for k, st in enumerate(s.steps) if st.writes)
        s.state['index'] = i; s.state['steps'][i]['status'] = 'running'
        self.server.Handler.session = s
        code, d = self.request('/api/history/clear', {'confirm': True})
        self.assertEqual((code, d['ok']), (409, False)); self.assertTrue(done.dir.exists())
        s.state['steps'][i]['status'] = 'pending'

    def test_home_is_refused_while_a_step_runs(self):
        s = self.server.open_session(True, True, 'map')
        i = next(k for k, st in enumerate(s.steps) if st.writes)
        s.state['index'] = i; s.state['steps'][i]['status'] = 'running'
        self.server.Handler.session = s
        code, d = self.request('/api/home', {})
        self.assertEqual((code, d['ok']), (409, False))
        self.assertIs(self.server.Handler.session, s)          # still open, nothing dropped
        s.state['steps'][i]['status'] = 'pending'               # done running: home works again
        self.assertEqual(self.request('/api/home', {})[0], 200)

    def test_exit_is_locked_while_a_write_runs(self):
        s = self.server.open_session(True, True, 'map')
        i = next(k for k, st in enumerate(s.steps) if st.writes)
        s.state['index'] = i; s.state['steps'][i]['status'] = 'running'
        self.server.Handler.session = s
        code, v = self.request('/api/state')
        self.assertTrue(v['lock']['locked']); self.assertTrue(v['lock']['writing'])
        code, d = self.request('/api/shutdown', {})
        self.assertEqual(code, 409); self.assertIn('Writing to the camera', d['error'])
        self.assertTrue(self.thread.is_alive())                       # the server kept running
        s.state['steps'][i]['status'] = 'pending'

    def test_shutdown_stops_the_server_when_idle(self):
        code, v = self.request('/api/state')
        self.assertFalse(v['lock']['locked'])
        code, d = self.request('/api/shutdown', {})
        self.assertEqual((code, d['ok']), (200, True))
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def test_resume_refuses_a_session_from_another_recipe_version(self):
        st = json.loads((self.map.dir / 'state.json').read_text())
        st['steps'] = st['steps'][:-1]                                  # the recipe has one step more than when it was saved
        (self.map.dir / 'state.json').write_text(json.dumps(st))
        code, d = self.request('/api/resume', {'folder': self.map.dir.name})
        self.assertEqual(code, 400); self.assertIn('different version', d['error'])
        row = {x['folder']: x for x in self.request('/api/sessions')[1]['sessions']}[self.map.dir.name]
        self.assertFalse(row['resumable'])

    def test_new_recipe_from_home(self):
        code, d = self.request('/api/new', {'recipe': 'rollback'})
        self.assertEqual(code, 200, d)
        self.assertTrue(d['ok'])
        self.assertEqual(self.server.Handler.session.recipe, 'rollback')
        self.assertEqual(self.server.Handler.session.state['index'], 0)


if __name__ == '__main__':
    unittest.main()
