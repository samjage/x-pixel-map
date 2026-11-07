"""The full pixel-mapping loop (PROCEDURE.md) as UI steps. The owner's camera handling is copied word for word."""
from engine import Step
import camera as K
import sort as S

WAIT = 5      # seconds for every "wait a few seconds" (owner: off -> wait -> battery out -> wait -> battery in)

# saved sessions the simulation replays (real frames from 2026-11-04/05, before = v11b / v6, after = v12), standing in for the scan's settings
_V11 = {200: ('2026-11-05_140555_v11b_iso200', 'dark_1s_iso200'), 800: ('2026-11-05_135941_v11b_iso800', 'dark_1s_iso800')}
_V12 = {200: ('2026-11-05_144623_v12_iso200', 'dark_1s_iso200'), 800: ('2026-11-05_144433_v12_iso800', 'dark_1s_iso800'),
        1600: ('2026-11-05_144548_v12_iso1600', 'dark_1s_iso1600')}
SIM = {'flat_iso200': ('2026-11-05_141233_v11b_white200', 'flat_iso200')}
for _name, _iso, *_ in S.SCAN:
    SIM[_name] = _V11[200 if _iso < 800 else 800]; SIM['after_' + _name] = _V12[200 if _iso < 800 else 800]
SIM['dark_short_iso800'] = ('2026-11-04_205935_v6_iso800', 'dark_short_iso800')
SIM['dark_4s_iso1250'] = ('2026-11-05_140436_v11b_iso1600', 'dark_1s_iso1600'); SIM['after_dark_4s_iso1250'] = _V12[1600]


def it(text, wait=0):
    return {'text': text, 'wait': wait}


def shot(id, phase, title, name, kind, iso, t, frames, skip_if=None):
    return Step(id, phase, title, 'run', fn=lambda s, p: K.shoot_set(s, p, name, kind, iso, t, frames, SIM.get(name)), skip_if=skip_if)


LEAVE_SERVICE = [it('Turn the camera off'), it('Wait a few seconds', WAIT), it('Pull the battery out'),
                 it('Wait a few seconds', WAIT), it('Put the battery back in')]
TO_PC_SHOOT = [it('Unplug the USB cable'), it('Power on and press through the date / time screen (the computer sets the clock later)'),
               it('MENU, press Left, then Up, to jump to the USB menu; choose USB mode: PC SHOOT AUTO'), it('Plug the USB cable back in')]
DARK_SETUP = [it('Lens cap on, lens stays on the camera'), it('Dark cloth over the camera, dim room'), it('Focus switch on M'),
              it('Shutter dial and ISO dial on A (the computer sets them)'),
              it('Shutter type: Mechanical, or Mechanical + Electronic')]
nothing_to_flash = lambda s: 'candidate' not in s.ctx
not_first_run = lambda s: s.simulate or (K.has_image() and not s.ctx.get('first_read'))


def save_summary(s):
    c = s.ctx['candidate']; base = s.ctx.get('base', {})
    return {'title': f'Write the new pixel map {c["sha12"]} to the camera',
            'lines': [f'The camera holds {c["base_sha12"]}; its backup stays locked as your rollback.'] +
                     [f'Change: {x}' for x in c['changes']] +
                     [f'{c["bytes"]} bytes differ. Checked offline' + ('' if s.simulate else ' and in the emulated camera (save, reboot, restore drill).'),
                      'The software writes, reads back to compare, then saves. Do not touch the camera or cable until it says done.'],
            'confirm': c['sha12']}


def restore_summary(s):
    st = s.ctx.get('settings', {})
    return {'title': 'Put your menu settings and clock back',
            'lines': [f'Writes the menu settings saved at the start ({st.get("sha12", "?")}) and sets the clock from this computer.',
                      'Service mode reset them; this is the same restore FUJIFILM X Acquire does. Your pixel fix is not touched.'],
            'confirm': st.get('sha12')}


def steps():
    return [
        Step('connect', 'Start', 'Connect the camera', 'instruct', why='Normal mode with USB = PC SHOOT lets the computer save your settings and take the test shots.',
             items=[it('Camera on in normal mode (no keys held)'), it('USB cable connected'),
                    it('MENU, press Left, then Up; USB mode: PC SHOOT AUTO'), it('Image quality RAW, auto power off OFF, long-exposure NR OFF')],
             wait_for='normal'),
        Step('settings', 'Start', 'Save your menu settings', 'run', why='Service mode resets every menu setting and the clock; this copy puts them back at the end.',
             fn=K.settings_backup),
        Step('first_service', 'Start', 'Into service mode, once', 'instruct', why='A new install has to read your camera once before it can compare anything.',
             wait_for='service', items=[it('Turn the camera off'), it('Wait a few seconds', WAIT), it('Keep USB connected; hold Shutter + Up while switching on')],
             skip_if=not_first_run),
        Step('first_read', 'Start', 'Back up your camera', 'run', fn=K.first_backup, skip_if=not_first_run),
        Step('first_normal', 'Start', 'Leave service mode', 'instruct', wait_for='normal', items=LEAVE_SERVICE + TO_PC_SHOOT, skip_if=not_first_run),
        Step('first_restore', 'Start', 'Restore settings and clock', 'approve', fn=K.settings_restore, summary=restore_summary, writes=True,
             skip_if=not_first_run),
        Step('dark_setup', 'Find', 'Set up for dark frames', 'instruct', why='With no light, any pixel that shows up is leaking.',
             items=DARK_SETUP),
        *[shot('shoot_' + n, 'Find', f'Dark frames, ISO {iso}, {lab}', n, 'dark', iso, t, S.SCAN_FRAMES) for n, iso, t, lab in S.SCAN],
        Step('white_setup', 'Find', 'Set up for white frames', 'instruct', why='Some pixels only misbehave under light; an even white screen shows them.',
             items=[it('Lens on, focus switch M, focus at infinity'), it('Aperture f/5.6'),
                    it('Phone screen all white at full brightness, lens almost touching it'), it('Shutter dial and ISO dial stay on A')]),
        shot('shoot_white', 'Find', 'White frames, ISO 200', 'flat_iso200', 'flat', 200, 1 / 30, 5),
        Step('sort', 'Find', 'Sort what was found', 'run', fn=S.classify),
        Step('review', 'Find', 'Choose what to fix', 'review', why='Ticked pixels go into the new pixel map. Pixels that are clearly visible are ticked for you.'),
        Step('build', 'Build', 'Build and check the new pixel map', 'run', fn=S.build),
        Step('service', 'Write', 'Into service mode', 'instruct', why='Only service mode accepts a new defect map.', wait_for='service',
             items=[it('Turn the camera off'), it('Wait a few seconds', WAIT), it('Keep USB connected; hold Shutter + Up while switching on')],
             skip_if=nothing_to_flash),
        Step('save', 'Write', 'Write the new pixel map', 'approve', fn=K.save_candidate, summary=save_summary, writes=True, skip_if=nothing_to_flash),
        Step('cycle', 'Write', 'Power cycle, back into service mode', 'instruct', why='Reading it back after a restart is the only proof the camera kept it.',
             wait_for='service', items=LEAVE_SERVICE + [it('Hold Shutter + Up while switching on')], skip_if=nothing_to_flash),
        Step('readback', 'Write', 'Read it back and lock the backup', 'run', fn=K.readback, skip_if=nothing_to_flash),
        Step('normal', 'Back to normal', 'Leave service mode', 'instruct', wait_for='normal', items=LEAVE_SERVICE + TO_PC_SHOOT,
             skip_if=nothing_to_flash),
        Step('restore', 'Back to normal', 'Restore settings and clock', 'approve', fn=K.settings_restore, summary=restore_summary, writes=True,
             skip_if=nothing_to_flash),
        Step('check_setup', 'Check', 'Set up for the check', 'instruct', why='The same dark frames as before, to compare.',
             items=DARK_SETUP, skip_if=nothing_to_flash),
        *[shot('after_' + n, 'Check', f'Check: ISO {iso}, {lab}', 'after_' + n, 'dark', iso, t, S.SCAN_FRAMES, nothing_to_flash) for n, iso, t, lab in S.SCAN],
        Step('end_200', 'Check', 'Finish up', 'instruct', items=[it('Set your dials back the way you like them'), it('Lens cap off')]),
        Step('report', 'Done', 'Before and after', 'result', fn=S.report),
    ]


# ---- roll back to any locked image ---------------------------------------------------------------------------
def restore_summary_choice(s):
    c = s.ctx['choice']; now = K.holds(s)['now'] or {}
    return {'title': f'Put {c["name"]} back into the camera',
            'lines': [f'Pixel map {c["sha12"]}, saved {c["date"]}, from its locked backup.',
                      f'The camera now holds {now.get("name", "?")} ({now.get("sha12", "?")}); its backup stays locked too, so this is undoable the same way.',
                      'The software writes, reads back to compare, then saves. Do not touch the camera or cable until it says done.'],
            'confirm': c['sha12']}


def rollback_steps():
    return [
        Step('connect', 'Start', 'Connect the camera', 'instruct', why='Normal mode first, so your menu settings can be saved before service mode resets them.',
             items=[it('Camera on in normal mode (no keys held)'), it('USB cable connected'), it('MENU, press Left, then Up; USB mode: PC SHOOT AUTO')],
             wait_for='normal'),
        Step('settings', 'Start', 'Save your menu settings', 'run', fn=K.settings_backup),
        Step('choose', 'Roll back', 'Choose the pixel map to put back', 'choose', why='Every pixel map that was ever read back from this camera is kept, locked.',
             fn=lambda s: K.backups_list(s)),
        Step('service', 'Roll back', 'Into service mode', 'instruct', wait_for='service',
             items=[it('Turn the camera off'), it('Wait a few seconds', WAIT), it('Keep USB connected; hold Shutter + Up while switching on')]),
        Step('restore_img', 'Roll back', 'Write the chosen pixel map', 'approve', fn=K.restore_backup, summary=restore_summary_choice, writes=True),
        Step('cycle', 'Roll back', 'Power cycle, back into service mode', 'instruct', wait_for='service',
             items=LEAVE_SERVICE + [it('Hold Shutter + Up while switching on')]),
        Step('readback', 'Roll back', 'Read it back', 'run', fn=K.readback_choice),
        Step('normal', 'Back to normal', 'Leave service mode', 'instruct', wait_for='normal', items=LEAVE_SERVICE + TO_PC_SHOOT),
        Step('restore', 'Back to normal', 'Restore settings and clock', 'approve', fn=K.settings_restore, summary=restore_summary, writes=True),
    ]


RECIPES = {'map': steps, 'rollback': rollback_steps}
