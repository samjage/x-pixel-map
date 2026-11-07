"""Step engine for the X Pixel Map session UI.

A session is a fixed list of steps (the recipe). Each step is one of:
  instruct  the owner does something physical; a checklist, some items with a minimum-wait timer; optional
            'wait_for' camera mode ('normal' / 'service') that must be seen on USB before Continue is allowed
  run       the software works (shots, analysis, build, verify, save...); reports progress, image, countdown
  approve   a camera write: shows exactly what will be written (file, sha12) and waits for the owner's press
  review    the found pixels with checkboxes; the owner's selection goes into the build
  result    the before/after report
State (step index, statuses, checklist ticks, context, log) is written to state.json after every change, so a
crash or a dropped USB link resumes where it stopped. Camera writes only happen inside approve steps, after the press.
"""
import json, re, threading, time, traceback
from pathlib import Path


class Step:
    def __init__(self, id, phase, title, kind, why='', items=None, wait_for=None, fn=None, summary=None, writes=False, skip_if=None):
        self.id, self.phase, self.title, self.kind, self.why = id, phase, title, kind, why
        self.items = items or []          # instruct: [{'text':..., 'wait': seconds}]
        self.wait_for = wait_for          # instruct: camera mode required before Continue
        self.fn = fn                      # run / approve / review / result: fn(session, progress) -> dict
        self.summary = summary            # approve: fn(session) -> {'title', 'lines', 'confirm'}
        self.writes = writes              # approve steps that write the camera
        self.skip_if = skip_if            # fn(session) -> True to skip (e.g. nothing to flash)


class StopRequest(Exception):
    pass


class Session:
    def __init__(self, folder, steps, camera_mode, simulate=False, view=False):
        self.dir = Path(folder); self.dir.mkdir(parents=True, exist_ok=True)
        self.steps, self.camera_mode, self.simulate = steps, camera_mode, simulate
        self.lock = threading.RLock()
        self.worker = None
        f = self.dir / 'state.json'
        if f.exists():
            try:
                self.state = json.loads(f.read_text())
            except Exception:
                raise ValueError('the saved session cannot be read')
            step_ids = [s['id'] for s in self.state.get('steps', [])]
            recipe_ids = [s.id for s in steps]
            if step_ids != recipe_ids and view and int(self.state.get('index') or 0) >= len(step_ids):
                known = {x.id: x for x in steps}                  # a finished session is only looked at: steps the recipe has since dropped get a plain stand-in
                steps = [known.get(i) or Step(i, 'Earlier version', i.replace('_', ' ').capitalize(), 'run') for i in step_ids]
                self.steps = steps; recipe_ids = step_ids
            if step_ids != recipe_ids:
                raise ValueError('this saved session was made by a different version of the recipe and cannot be resumed')
            for s in self.state['steps']:            # a run that was cut off by a crash must be re-run, never assumed done
                if s['status'] == 'running': s['status'] = 'pending'; s['note'] = 'interrupted; press Retry'
        else:
            self.state = {'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'index': 0, 'simulate': simulate,
                          'steps': [{'id': s.id, 'status': 'pending', 'ticks': {}, 'note': ''} for s in steps],
                          'ctx': {}, 'log': [], 'activity': {}}
        self.save()

    # ---- persistence / log -------------------------------------------------------------------------------
    def save(self):
        with self.lock:
            tmp = self.dir / 'state.json.tmp'
            tmp.write_text(json.dumps(self.state, indent=1, default=str)); tmp.replace(self.dir / 'state.json')

    def log(self, text, level='info'):
        with self.lock:
            self.state['log'].append({'t': time.strftime('%H:%M:%S'), 'text': text, 'level': level})
            self.state['log'] = self.state['log'][-300:]
            self.save()

    @property
    def ctx(self):
        return self.state['ctx']

    def progress(self, text=None, frac=None, countdown=None, image=None, detail=None):
        """What the software is doing right now (shown live in the UI)."""
        with self.lock:
            a = self.state['activity']
            if text is not None: a['text'] = text; self.log(text)
            if frac is not None: a['frac'] = frac
            if countdown is not None: a['countdown_end'] = time.time() + countdown; a['countdown_total'] = countdown
            if image is not None: a['image'] = str(image)
            if detail is not None: a['detail'] = detail
            self.save()

    # ---- view for the UI ---------------------------------------------------------------------------------
    def view(self):
        camera = self.camera_mode()          # lsusb can be slow while the camera reboots; never hold the lock for it
        with self.lock:
            now = time.time()
            out = {'folder': self.dir.name, 'created': self.state['created'], 'index': self.state['index'], 'simulate': self.simulate,
                   'camera': camera, 'ctx': self.state['ctx'], 'log': self.state['log'][-40:],
                   'activity': dict(self.state['activity'], now=now), 'steps': []}
            for st, s in zip(self.steps, self.state['steps']):
                items = []
                for i, it in enumerate(st.items):
                    tk = s['ticks'].get(str(i))
                    status = 'todo'
                    if tk == 'done': status = 'done'
                    elif isinstance(tk, (int, float)): status = 'done' if now >= tk else 'timing'
                    items.append({'text': it['text'], 'wait': it.get('wait', 0), 'status': status,
                                  'end': tk if isinstance(tk, (int, float)) else None})
                d = {'id': st.id, 'phase': st.phase, 'title': st.title, 'kind': st.kind, 'why': st.why,
                     'status': s['status'], 'note': s.get('note', ''), 'items': items, 'wait_for': st.wait_for,
                     'result': s.get('result'), 'writes': st.writes}
                if st.kind == 'choose' and self.state['index'] == self.steps.index(st):
                    try: d['choices'] = st.fn(self)
                    except Exception as e: d['choices'] = []; d['note'] = str(e)
                if st.kind == 'approve' and s['status'] in ('pending', 'ready') and self.state['index'] == self.steps.index(st):
                    try: d['summary'] = st.summary(self)
                    except Exception as e: d['summary'] = {'title': 'cannot prepare this step', 'lines': [str(e)], 'confirm': None}
                out['steps'].append(d)
            return out

    # ---- actions from the UI -----------------------------------------------------------------------------
    def current(self):
        i = self.state['index']
        return (self.steps[i], self.state['steps'][i]) if i < len(self.steps) else (None, None)

    def act(self, action, **kw):
        with self.lock:
            st, s = self.current()
            if st is None: return {'ok': False, 'error': 'session finished'}
            if action == 'tick' and st.kind == 'instruct':
                try: i = int(kw['item'])
                except (KeyError, TypeError, ValueError): return {'ok': False, 'error': 'no such checklist item'}
                if not 0 <= i < len(st.items): return {'ok': False, 'error': 'no such checklist item'}
                it = st.items[i]
                if s['ticks'].get(str(i)) in (None,):
                    s['ticks'][str(i)] = time.time() + it['wait'] if it.get('wait') and not self.simulate else 'done'   # simulation: no waiting
                self.save(); return {'ok': True}
            if action == 'continue' and st.kind == 'instruct':
                now = time.time()
                for i in range(len(st.items)):
                    tk = s['ticks'].get(str(i))
                    if tk is None or (isinstance(tk, (int, float)) and now < tk):
                        return {'ok': False, 'error': 'finish every item first (timers must run out)'}
                if st.wait_for and self.camera_mode() != st.wait_for:
                    return {'ok': False, 'error': f'waiting for the camera in {st.wait_for} mode on USB'}
                return self._finish(s, {'note': 'done'})
            if action == 'approve' and st.kind == 'approve':
                summ = st.summary(self)
                if summ.get('confirm') and kw.get('confirm') != summ['confirm']:
                    return {'ok': False, 'error': 'confirmation does not match this step'}
                self.log(f'owner approved: {summ["title"]}', 'approve')
                return self._start(st, s)
            if action == 'choose' and st.kind == 'choose':
                c = next((c for c in st.fn(self) if c['sha12'] == kw.get('value')), None)
                if not c: return {'ok': False, 'error': 'pick one of the listed images'}
                self.ctx['choice'] = c; self.log(f'owner chose {c["name"]} ({c["sha12"]})', 'approve')
                return self._finish(s, {'chosen': c['name'], 'sha12': c['sha12']})
            if action == 'review' and st.kind == 'review':
                sel = kw.get('selected', [])
                if not (isinstance(sel, list) and all(isinstance(x, str) and re.fullmatch(r'\d+,\d+', x) for x in sel)):
                    return {'ok': False, 'error': 'invalid pixel selection'}
                self.ctx['selected'] = sel
                self.log(f'owner selected {len(self.ctx["selected"])} pixels to fix', 'approve')
                return self._finish(s, {'selected': len(self.ctx['selected'])})
            if action in ('retry', 'start') and st.kind in ('run', 'result') and s['status'] in ('pending', 'failed'):
                return self._start(st, s)
            if action == 'back' and self.state['index'] > 0 and s['status'] != 'running':
                prev = self.state['steps'][self.state['index'] - 1]
                if self.steps[self.state['index'] - 1].kind in ('instruct', 'review'):
                    self.state['index'] -= 1; prev['status'] = 'pending'; prev['ticks'] = {}; self.save(); return {'ok': True}
                return {'ok': False, 'error': 'the previous step already ran; it cannot be undone from here'}
            return {'ok': False, 'error': f'{action} is not possible at this step'}

    def _finish(self, s, result):
        s['status'] = 'done'; s['result'] = result
        self.state['index'] += 1; self.state['activity'] = {}
        while True:                                   # steps that do not apply this time are marked skipped
            st, nxt = self.current()
            if st is None or not st.skip_if or not st.skip_if(self): break
            nxt['status'] = 'skipped'; self.state['index'] += 1
        self.save(); self.autostart()
        return {'ok': True}

    def _start(self, st, s):
        if self.worker and self.worker.is_alive(): return {'ok': False, 'error': 'busy'}
        s['status'] = 'running'; s['note'] = ''; self.save()

        def work():
            try:
                res = st.fn(self, self.progress) or {}
                with self.lock:
                    self.worker = None              # so the next run step can start from here
                    self._finish(s, res)
            except StopRequest as e:
                with self.lock: s['status'] = 'failed'; s['note'] = str(e); self.save()
                self.log(str(e), 'stop')
            except Exception as e:
                with self.lock: s['status'] = 'failed'; s['note'] = f'{type(e).__name__}: {e}'; self.save()
                self.log(f'{st.title} failed: {e}', 'error'); self.log(traceback.format_exc()[-800:], 'debug')
        self.worker = threading.Thread(target=work, daemon=True); self.worker.start()
        return {'ok': True}

    def autostart(self):
        """run / result steps start by themselves; approve and instruct wait for the owner."""
        st, s = self.current()
        if st is not None and st.kind in ('run', 'result') and s['status'] == 'pending':
            self._start(st, s)
