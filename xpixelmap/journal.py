"""Write-ahead session journal: every step is recorded (fsync) BEFORE it is sent to the camera, and its outcome
after. journal.jsonl is append-only; state.json is the latest summary (atomically replaced).
`interpret()` turns the last recorded step into what is true on the camera and what to do next."""
import datetime
import json
import os
from pathlib import Path


def utcstamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')


class Journal:
    def __init__(self, root, label):
        root = Path(root); root.mkdir(parents=True, exist_ok=True)
        base = f'{utcstamp()}-{label}'
        for i in range(1000):
            d = root / (base if i == 0 else f'{base}-{i}')
            try:
                d.mkdir(); break
            except FileExistsError:
                continue
        else:
            raise RuntimeError('cannot create a new session directory')
        self.dir = d
        self.path = d / 'journal.jsonl'
        self.state_path = d / 'state.json'
        self.seq = 0
        self.state = {'session': d.name, 'phase': 'start', 'saved': False, 'ram_modified': False}

    def event(self, name, **fields):
        self.seq += 1
        rec = {'seq': self.seq, 'time': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'event': name}
        rec.update(fields)
        with open(self.path, 'a') as f:
            f.write(json.dumps(rec) + '\n'); f.flush(); os.fsync(f.fileno())
        self.state.update(fields)
        self.state['phase'] = name
        if name in ('write_begin', 'write_progress', 'write_failed', 'write_done'):
            self.state['ram_modified'] = True
        if name == 'save_ok':
            self.state['saved'] = True
        if name == 'save_begin':
            self.state['saved'] = 'unknown'
        tmp = self.state_path.with_suffix('.tmp')
        with open(tmp, 'w') as f:
            json.dump(self.state, f, indent=1); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, self.state_path)
        return rec


def read_events(session_dir):
    events = []
    with open(Path(session_dir) / 'journal.jsonl') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                events.append({'event': 'corrupt_journal_line'})   # torn last line: treat as unknown
    return events


ADVICE = {
    'nothing_sent': 'No write and no save were sent in this session. Camera unchanged.',
    'ram_dirty': 'Camera RAM may hold a partial or unverified image. NOTHING WAS SAVED: flash is unchanged. '
                 'Power-cycle the camera (battery out/in) to discard the RAM copy, then start a new session.',
    'ram_verified': 'Camera RAM holds the verified image, NOT saved. A power-cycle discards it; nothing persistent '
                    'changed.',
    'save_unknown': 'SAVE OUTCOME UNKNOWN: the save command was sent but no success response was recorded. The flash '
                    'may hold the old image, the new image or a mix of both (save is not atomic). Do not power-cycle '
                    'for at least 60 s, then battery out/in, enter service mode (Shutter+Up), run `backup` and '
                    'compare. If it equals neither the backup nor the intended image, run `restore`.',
    'save_error': 'The camera answered the save with an error status. Flash state unknown: battery out/in, service '
                  'mode, `backup`, then `restore` if it differs from the backup.',
    'saved': 'Save reported success (status 22 00). Wait, battery out/in, power on, then in service mode run '
             '`backup --expect <image>` to confirm what the flash really holds.',
    'refused': 'The tool refused before writing anything. Camera unchanged.',
}


def interpret(events):
    """Return (key, text) describing the camera state implied by the last journal events."""
    names = [e.get('event') for e in events]
    if 'save_ok' in names:
        return 'saved', ADVICE['saved']
    if 'save_error' in names:
        return 'save_error', ADVICE['save_error']
    if 'save_begin' in names:
        return 'save_unknown', ADVICE['save_unknown']
    wrote = any(n in ('write_begin', 'write_progress', 'write_failed', 'write_done') for n in names)
    if wrote:
        if 'readback_ok' in names and names.index('readback_ok') > max(
                i for i, n in enumerate(names) if n in ('write_begin', 'write_progress', 'write_failed', 'write_done')):
            return 'ram_verified', ADVICE['ram_verified']
        last = [e for e in events if e.get('event') in ('write_progress', 'write_failed')]
        where = f" (last confirmed chunk end {last[-1].get('next_offset', last[-1].get('offset', '?'))})" if last else ''
        return 'ram_dirty', ADVICE['ram_dirty'] + where
    if any(n == 'refused' for n in names):
        return 'refused', ADVICE['refused']
    return 'nothing_sent', ADVICE['nothing_sent']
