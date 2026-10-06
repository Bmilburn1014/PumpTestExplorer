# server.py — Application entry point

import sys
import os
from pathlib import Path

# ── Resolve base directory for frozen .exe ───────────────────
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

# Ensure the working directory is the app directory
os.chdir(str(BASE_DIR))

# Verify assets exist (helps debug .exe issues)
# PyInstaller 6.x puts bundled data in _internal/ next to the .exe
assets_dir = str(BASE_DIR / "assets")
if not os.path.isdir(assets_dir):
    # Check _internal (PyInstaller 6.x default)
    internal = BASE_DIR / "_internal" / "assets"
    if internal.is_dir():
        assets_dir = str(internal)
    else:
        # Check _MEIPASS (one-file mode)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and os.path.isdir(os.path.join(meipass, "assets")):
            assets_dir = os.path.join(meipass, "assets")

import dash
from layout.main_layout import build_layout

app = dash.Dash(
    __name__,
    assets_folder=assets_dir,
    suppress_callback_exceptions=True,
)

app.layout = build_layout()

import callbacks.comparison_callbacks   # noqa: F401
import callbacks.chart_callbacks        # noqa: F401
import callbacks.export_callbacks       # noqa: F401

from manual_mode import register_manual_callbacks
register_manual_callbacks(app)

if __name__ == "__main__":
    import threading
    import socket
    import time
    import subprocess
    import shutil

    PORT = 8050
    HOST = "127.0.0.1"
    URL = f"http://{HOST}:{PORT}"

    def _wait_for_port(host, port, timeout=30):
        """Poll until the Dash app is fully serving pages."""
        from urllib.request import urlopen
        from urllib.error import URLError
        url = f"http://{host}:{port}/"
        start = time.time()
        while time.time() - start < timeout:
            try:
                resp = urlopen(url, timeout=2)
                if resp.status == 200:
                    return True
            except (URLError, OSError, ConnectionRefusedError):
                time.sleep(0.5)
        return False

    def _find_browser():
        """Find Chrome or Edge executable for app-mode window."""
        import platform
        if platform.system() != "Windows":
            return None

        candidates = [
            os.path.expandvars(
                r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(
                r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(
                r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(
                r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(
                r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        return None

    def _open_app_window(url):
        """
        Open URL in a standalone app-mode window (no address bar).
        Returns the subprocess handle so we can detect when it closes.
        """
        browser = _find_browser()
        if browser:
            # Use a dedicated user-data-dir so Chrome/Edge launches as
            # its own process (not delegating to an existing instance).
            # This lets us detect when the window is closed.
            import tempfile
            user_data = tempfile.mkdtemp(prefix="pump_explorer_")
            proc = subprocess.Popen([
                browser,
                f"--app={url}",
                f"--window-size=1400,900",
                f"--user-data-dir={user_data}",
                "--no-first-run",
                "--no-default-browser-check",
            ])
            return proc, user_data
        else:
            import webbrowser
            webbrowser.open(url)
            return None, None

    def _run_server():
        app.run(host=HOST, port=PORT, debug=False, use_reloader=False)

    # 1. Start the Dash server in a background thread
    server_thread = threading.Thread(target=_run_server, daemon=True)
    server_thread.start()

    # 2. Wait until the server is actually listening
    browser_proc = None
    temp_dir = None
    if _wait_for_port(HOST, PORT):
        # 3. Open standalone window only after server is ready
        browser_proc, temp_dir = _open_app_window(URL)
    else:
        print(f"ERROR: Server failed to start on {HOST}:{PORT}")

    # 4. Monitor the browser — exit when the user closes the window
    try:
        if browser_proc:
            # Poll until the browser process exits
            while browser_proc.poll() is None:
                time.sleep(1)
        else:
            # No browser handle (fallback mode) — keep alive until Ctrl+C
            while server_thread.is_alive():
                server_thread.join(timeout=1)
    except KeyboardInterrupt:
        pass
    finally:
        # Clean up the temp browser profile
        if temp_dir:
            import shutil as _shutil
            try:
                _shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass