"""App window for the UI (Linux: GTK WebKit through pywebview). It shows the same page as the browser view and adds a
real close lock: the window's close button always goes through the page's Exit dialog, which is refused while a step
runs. The window closes only after the server has stopped, so closing it never leaves a server behind.
"""
import os, signal, threading

# WebKitGTK's DMA-BUF renderer dies with "Error 71 (Protocol error)" on Wayland with NVIDIA drivers (seen on Hyprland,
# RTX 5070 Ti). The older path renders the same page; set before WebKit loads, and an explicit setting still wins.
os.environ.setdefault('WEBKIT_DISABLE_DMABUF_RENDERER', '1')


def available():
    """True when pywebview and the GTK WebKit bindings it uses on Linux can be loaded."""
    try:
        import webview  # noqa: F401
        import gi
        gi.require_version('Gtk', '3.0'); gi.require_version('WebKit2', '4.1')
        return True
    except Exception:
        return False


def run(url, serving, stop, busy):
    """Blocks until the window is gone. serving: the server thread; stop(): asks the server to stop (it refuses while
    a step runs); busy(): the lock reason or ''."""
    import webview
    from gi.repository import GLib
    GLib.set_prgname('x-pixel-map'); GLib.set_application_name('X Pixel Map')   # window class for the desktop
    closing_ok = threading.Event()
    win = webview.create_window('X Pixel Map', url, width=1440, height=920, min_size=(900, 640),
                                background_color='#0f1114', text_select=True)

    def on_closing():
        if closing_ok.is_set(): return True
        # never close straight away: the page's Exit dialog warns, or says why it is locked (evaluate_js waits on the
        # GTK loop, so it must not run inside this handler)
        threading.Thread(target=lambda: win.evaluate_js('exitApp()'), daemon=True).start()
        return False
    win.events.closing += on_closing

    def close_when_stopped():
        serving.join()
        closing_ok.set()
        win.destroy()

    def on_sigint():
        reason = busy()
        if reason: print(f'X Pixel Map: {reason} Ctrl+C ignored until it has finished.')
        else: threading.Thread(target=stop, daemon=True).start()
        return True                                    # keep the handler for the next Ctrl+C

    def started():
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, on_sigint)   # GTK owns the main thread
        close_when_stopped()

    webview.start(started, gui='gtk', private_mode=True)
