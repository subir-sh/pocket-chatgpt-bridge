"""Windows idle-sleep inhibition while the local bridge is running.

Only ES_SYSTEM_REQUIRED is set: the display is deliberately NOT kept awake.
SetThreadExecutionState is thread-scoped, so the assertion and release must
run on the same long-lived thread. We neither change the global power plan
nor attempt to override explicit sleep/lid-close/lock actions.
"""
from __future__ import annotations

import ctypes
import sys
import threading
from collections.abc import Callable

ES_SYSTEM_REQUIRED = 0x00000001
ES_CONTINUOUS = 0x80000000


def windows_execution_state(flags: int) -> int:
    """Invoke the Windows power API, raising on failure (not available on other OSes)."""
    if sys.platform != "win32":
        raise RuntimeError("Sleep inhibition requires Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    func = kernel32.SetThreadExecutionState
    func.argtypes = [ctypes.c_uint32]
    func.restype = ctypes.c_uint32
    old_state = func(flags)
    if not old_state:
        raise ctypes.WinError(ctypes.get_last_error())
    return old_state


class SleepGuard:
    """Hold ES_SYSTEM_REQUIRED for the bridge lifetime on a dedicated thread."""

    def __init__(self, setter: Callable[[int], int] = windows_execution_state):
        self._setter = setter
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None
        self._active = False

    @property
    def active(self) -> bool:
        return self._active and self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        acquired = False
        try:
            self._setter(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            acquired = True
            self._active = True
        except BaseException as exc:
            self._error = exc
        finally:
            self._ready.set()

        if acquired:
            try:
                self._stop.wait()
            finally:
                try:
                    # Must execute from the same thread that asserted the state.
                    self._setter(ES_CONTINUOUS)
                finally:
                    self._active = False

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Sleep guard already started")
        self._thread = threading.Thread(target=self._run, name="windows-sleep-guard", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5):
            self.stop()
            raise RuntimeError("Sleep guard did not start")
        if self._error is not None:
            self.stop()
            raise RuntimeError(f"Could not prevent Windows idle sleep: {self._error}") from self._error

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError("Sleep guard did not stop")
