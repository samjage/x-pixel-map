# X-T1 pixel mapping: the procedure (as run on 2026-11-05)

Everything after "Detect" can repeat for a re-map. Claude runs every command; the owner handles the camera.
Each camera write needs the owner's go for that one image. Never `ff80 syslog`, never firmware upload.

## Camera handling (the owner's exact steps)

**Into service mode:** camera off, USB connected, hold **Shutter + Up** while switching on.

**Leaving service mode:**
1. Turn the camera off.
2. Wait a few seconds.
3. Pull the battery.
4. Wait a few seconds.
5. Put the battery back.

**Into USB = PC SHOOT AUTO** (needed after every service-mode visit; service mode resets date/time and all menus):
1. Unplug the USB cable.
2. Power on.
3. Press through the date/time screen.
4. MENU, press **Up** once to jump straight to the bottom (USB) menu, select USB mode, choose **PC SHOOT AUTO**.
5. Plug the USB cable back in.

Claude then restores the menu settings and the clock from the PC (step 6 below), so the date entered on the camera does not matter.

**Dials for test shots:** shutter dial and ISO dial on **A** (the PC sets both, tested 2026-11-06), focus switch **M**, shutter type Mechanical or Mechanical + Electronic, long-exposure NR off.

## Steps

1. **Save settings** (normal mode, USB = PC SHOOT AUTO): `python3 tools/capture/xt1settings.py backup` (read-only; sets priority to PC itself).
2. **Detect:** 5 dark frames each at ISO 200 1/4 s, 250 1 s, 320 1/4 s, 400 1/4 s, 800 1/200 s and 1250 4 s (lens cap on, lens stays on, dark cloth), judged frame by frame: above +100 in every frame of two sets (or of one set up to ISO 1000), or above +200 in single frames in two sets, then a white flat at ISO 200 (lens on, f/5.6, manual focus at infinity,
   phone screen all white). `xt1capture.py capture --plan sweep|verify|white --sets ...`, then `xt1capture.py analyze <session>`.
3. **Sort** what was found:
   - ordinary hot pixel (grows with ISO) -> list0 + list2
   - coupled pair (hot in short frames, left neighbour leaks in long frames) -> Fuji-style pixel + left partner in list2, joint aux tags
   - light-sensitive (white flat only) -> lists
   - focus-grid pixel -> nothing per pixel: cfg 0x113b6 = 0 (all 28 classes, in v12) already suppresses it
   - faint spots that differ per ISO and do not grow with ISO -> ignore (processing pattern, not defects)
4. **Build** offline (helper/edit_lists.py / xpixelmap build), then `python3 -m xpixelmap verify --base <current camera backup> <candidate>`.
5. **Flash:** service mode; `python3 -m xpixelmap save --real --base <current backup> --confirm <sha12> <candidate>`; leave service mode
   (steps above) but go straight back into service mode; `python3 -m xpixelmap backup --real --expect <candidate>` (the camera's save handler
   ignores flash errors, so this read-back is the only proof); lock the new backup and copy it to a second disk (set `XPIXELMAP_SAFE_COPY`).
6. **Back to normal:** leave service mode, set USB = PC SHOOT AUTO (steps above); `xt1settings.py restore <backup> --confirm <sha12>` and
   `xt1settings.py clock`.
7. **Validate:** the same dark and white sets; the analyzer prints `FIX CHECK ... FIXED / STILL HOT`, the focus-site own share and white-light ratios.

All commands run from the project folder.
