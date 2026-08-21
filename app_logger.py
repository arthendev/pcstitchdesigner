"""General-purpose application logger.

Records timestamped send/receive byte traffic and high-level event lines
into a single session log file.  Used by :mod:`machine_comm` for serial
traffic and by the rest of the app for application events (file open/save,
startup, errors, …).
"""

import os
from datetime import datetime


class AppLogger:
    """Buffered direction-aware application logger.

    Accumulates bytes sent in one direction and flushes a timestamped line
    whenever the direction changes or the logger is closed.  The timestamp
    reflects when the batch started (first send/receive), not when it was
    flushed to disk.

    High-level event lines are written immediately via :meth:`log_info`,
    flushing any pending byte-traffic first so the event stays in order.

    The logger can be activated/deactivated via :meth:`set_enabled`; while
    deactivated every logging call is a no-op, so callers can hold a logger
    unconditionally.
    """

    def __init__(self, log_dir=None, enabled=True):
        self._file = None
        self._log_dir = log_dir
        self._current_direction = None  # 'send' or 'receive'
        self._buffer = bytearray()
        # Timestamp captured when the current batch started, so log lines
        # reflect when the transfer was executed rather than when it was flushed.
        self._start_time = None
        # A logger without a destination cannot be active.
        self._enabled = bool(enabled) and bool(log_dir)

    @property
    def enabled(self):
        """Return True if this logger is currently active."""
        return self._enabled

    def set_enabled(self, enabled):
        """Activate or deactivate this logger.

        Deactivating flushes and closes any open log file; the file is
        re-created lazily on the next write after reactivating.
        """
        enabled = bool(enabled) and bool(self._log_dir)
        if enabled == self._enabled:
            return
        if not enabled:
            self.close()
        self._enabled = enabled

    def activate(self):
        """Activate logging (alias for ``set_enabled(True)``)."""
        self.set_enabled(True)

    def deactivate(self):
        """Deactivate logging (alias for ``set_enabled(False)``)."""
        self.set_enabled(False)

    def _ensure_file(self):
        if self._file is None:
            os.makedirs(self._log_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"log_{timestamp}.txt"
            self._file = open(os.path.join(self._log_dir, filename), "w", encoding="utf-8")

    def _flush(self):
        if self._buffer and self._current_direction is not None:
            ts = (self._start_time or datetime.now()).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            arrow = "->" if self._current_direction == "send" else "<-"
            hex_bytes = " ".join(f"{b:02X}" for b in self._buffer)
            self._file.write(f"[{ts}] {arrow} {hex_bytes}\n")
            self._file.flush()
            self._buffer.clear()
            self._start_time = None

    def log_send(self, data):
        """Record bytes sent to the machine, buffered per direction."""
        if not self._enabled:
            return
        self._ensure_file()
        if self._current_direction == "receive":
            self._flush()
        if self._start_time is None:
            self._start_time = datetime.now()
        self._current_direction = "send"
        self._buffer.extend(data)

    def log_receive(self, data):
        """Record bytes received from the machine, buffered per direction."""
        if not self._enabled:
            return
        self._ensure_file()
        if self._current_direction == "send":
            self._flush()
        if self._start_time is None:
            self._start_time = datetime.now()
        self._current_direction = "receive"
        self._buffer.extend(data)

    def log_info(self, message):
        """Write a high-level event line, flushing any pending data first."""
        if not self._enabled:
            return
        self._ensure_file()
        self._flush()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        self._file.write(f"[{ts}] ## {message}\n")
        self._file.flush()

    def log_error(self, message):
        """Write a high-level error event line, flushing any pending data first."""
        self.log_info(f"ERROR: {message}")

    def close(self):
        """Flush any buffered data and close the log file."""
        if self._file is not None:
            self._flush()
            self._file.close()
            self._file = None
