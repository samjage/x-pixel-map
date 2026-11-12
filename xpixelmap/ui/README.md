# X Pixel Map UI (Linux for now)

## Setup (once)

The UI runs from the project environment `.venv`, built on the system Python so it uses the system's GTK, Pillow and
libusb bindings (Arch: `python-gobject`, `webkit2gtk-4.1`, `python-pillow`, `python-libusb1`):

```bash
uv venv --python /usr/bin/python3 --system-site-packages .venv
uv pip install --python .venv/bin/python numpy pywebview unicorn
```

## Run

```bash
.venv/bin/python -m xpixelmap.ui              # app window, resumes the last unfinished session
.venv/bin/python -m xpixelmap.ui --new        # app window, home menu
.venv/bin/python -m xpixelmap.ui --simulate --new --port 8766   # simulation: no camera, saved frames
```

- `--browser`: no window; serve only and print the address (open it in any browser).
- No app window available (pywebview or GTK WebKit missing): the page opens in the default browser instead.
- **How it works** in the header (and the Guide card on the home menu) opens a plain-language page about hot pixels,
  what the camera already does, and exactly what the tool changes. Escape or **Back to the app** returns to where you were.

## Closing

- The window's close button always opens the page's Exit dialog; while a step runs it is refused and says why.
- Closing the window stops the local server. Ctrl+C in the terminal does the same, and waits for a running step.
- In a browser tab the page can only ask ("Leave site?"); a write still finishes in the server if the tab closes.
- WebKitGTK's DMA-BUF renderer crashes on Wayland with NVIDIA (`Error 71 (Protocol error)`), so `window.py` sets
  `WEBKIT_DISABLE_DMABUF_RENDERER=1` unless it is already set.

## History and disk space

- The home screen has a **Past sessions** card: open a finished session to see its before and after and the pixels it fixed
  (read-only). The latest opens first; a date drop-down on the finished screen goes back.
- After a real run finishes, each set of shots is saved as a small compressed file (`archive/*.npz`, a few MB for the whole
  session) and the large RAW files are deleted, so a session costs tens of MB, not over a gigabyte. Set `XPIXELMAP_KEEP_FRAMES=1`
  to keep the RAW files. The simulation never deletes anything.
- **Clear history…** on that card removes the finished sessions after a confirmation. It never touches an unfinished session or
  the pixel-map backups that Roll back uses, and it is refused while a step is running.

## Tests

```bash
.venv/bin/python -m unittest discover -s xpixelmap/ui/tests
```
