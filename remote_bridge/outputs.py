"""Switch external video outputs off and restore the previous Windows topology."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import threading


class DisplayOutputs:
    def __init__(self):
        self.lock = threading.Lock()
        self.saved_file = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "ChatGPTRemoteController/display-topology.json"

    @staticmethod
    def topology():
        import win32api
        win32api.EnumDisplayMonitors()
        api = ctypes.WinDLL("user32", use_last_error=True)
        paths, modes, topology = wintypes.UINT(), wintypes.UINT(), wintypes.UINT()
        result = api.GetDisplayConfigBufferSizes(4, ctypes.byref(paths), ctypes.byref(modes))
        if result:
            raise OSError(result, "화면 구성을 읽지 못했어요.")
        # Opaque buffers: the native API owns structure layout; only topologyId is read.
        path_buffer = ctypes.create_string_buffer(max(1, paths.value) * 256)
        mode_buffer = ctypes.create_string_buffer(max(1, modes.value) * 256)
        result = api.QueryDisplayConfig(4, ctypes.byref(paths), path_buffer, ctypes.byref(modes), mode_buffer, ctypes.byref(topology))
        if result:
            raise OSError(result, "화면 구성을 읽지 못했어요.")
        return topology.value

    def set_external(self, enabled):
        import win32api
        with self.lock:
            if enabled:
                previous = json.loads(self.saved_file.read_text()) if self.saved_file.exists() else {"topology": 4}
                target = int(previous["topology"])
                if target not in (2, 4, 8):
                    target = 4
            else:
                current = self.topology()
                if current != 1 and not self.saved_file.exists():
                    self.saved_file.parent.mkdir(parents=True, exist_ok=True)
                    self.saved_file.write_text(json.dumps({"topology": current}), encoding="utf-8")
                target = 1
            result = ctypes.WinDLL("user32", use_last_error=True).SetDisplayConfig(0, None, 0, None, 0x80 | target)
            if result:
                raise OSError(result, "Windows에서 외부 화면 출력을 변경하지 못했어요.")
            if enabled:
                self.saved_file.unlink(missing_ok=True)
            return {"external_enabled": enabled, "screens": len(win32api.EnumDisplayMonitors()),
                    "note": "외부 모니터 출력을 다시 켰어요." if enabled else "외부 모니터 출력을 끄고 노트북 화면만 남겼어요."}
