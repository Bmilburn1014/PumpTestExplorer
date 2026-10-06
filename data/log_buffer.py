# data/log_buffer.py
#
# Thread-safe ring buffer for status bar messages.
# Both callbacks and engine write here; a dcc.Interval polls it.

import threading
from collections import deque
from datetime import datetime

_lock = threading.Lock()
_buffer = deque(maxlen=200)   # Keep last 200 messages
_counter = 0                   # Monotonic counter for change detection
_progress = 0                  # 0-100 progress value
_progress_label = ""           # e.g., "Loading 34/102..."


def log(message: str):
    """Add a message to the log buffer."""
    global _counter
    ts = datetime.now().strftime("%H:%M:%S")
    with _lock:
        _counter += 1
        _buffer.append(f"[{ts}] {message}")


def set_progress(pct: int, label: str = ""):
    """Set the progress bar value (0-100)."""
    global _progress, _progress_label
    with _lock:
        _progress = max(0, min(100, pct))
        _progress_label = label


def get_progress() -> tuple[int, str]:
    """Get current progress value and label."""
    with _lock:
        return _progress, _progress_label


def get_recent(n: int = 2) -> list[str]:
    """Get the N most recent messages."""
    with _lock:
        items = list(_buffer)
    return items[-n:] if items else []


def get_counter() -> int:
    """Get current counter for change detection."""
    with _lock:
        return _counter