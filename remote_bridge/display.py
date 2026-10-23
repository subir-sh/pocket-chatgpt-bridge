"""Built-in laptop brightness control, without suspending the display or desktop."""
import json
import os
from pathlib import Path
import threading


class BrightnessControl:
    def __init__(self):
        self.lock = threading.Lock()
        self.saved_file = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "ChatGPTRemoteController/brightness.json"

    def set_dimmed(self, dimmed):
        import pythoncom
        import win32com.client
        with self.lock:
            pythoncom.CoInitialize()
            try:
                service = win32com.client.GetObject(r"winmgmts:\\.\root\WMI")
                readings = {str(row.InstanceName): int(row.CurrentBrightness)
                            for row in service.ExecQuery("SELECT * FROM WmiMonitorBrightness WHERE Active=True")}
                if not readings:
                    raise RuntimeError("이 화면은 Windows 밝기 조절을 지원하지 않아요.")
                saved = json.loads(self.saved_file.read_text()) if self.saved_file.exists() else {}
                if dimmed:
                    for name, level in readings.items():
                        saved.setdefault(name, level)
                    self.saved_file.parent.mkdir(parents=True, exist_ok=True)
                    self.saved_file.write_text(json.dumps(saved), encoding="utf-8")
                elif not saved:
                    return {"dimmed": False, "note": "복원할 밝기 변경이 없어요."}
                for monitor in service.ExecQuery("SELECT * FROM WmiMonitorBrightnessMethods WHERE Active=True"):
                    name = str(monitor.InstanceName)
                    if name not in readings or (not dimmed and name not in saved):
                        continue
                    level = 0 if dimmed else int(saved[name])
                    parameters = monitor.Methods_("WmiSetBrightness").InParameters.SpawnInstance_()
                    parameters.Properties_("Timeout").Value = 0
                    parameters.Properties_("Brightness").Value = level
                    result = monitor.ExecMethod_("WmiSetBrightness", parameters)
                    if result is not None and int(result.Properties_("ReturnValue").Value) != 0:
                        raise RuntimeError("Windows에서 밝기 변경을 완료하지 못했어요.")
                    if not dimmed:
                        saved.pop(name, None)
                if not dimmed:
                    if saved:
                        self.saved_file.write_text(json.dumps(saved), encoding="utf-8")
                    else:
                        self.saved_file.unlink(missing_ok=True)
                return {"dimmed": dimmed, "note": "노트북 밝기를 최소로 낮췄어요." if dimmed else "이전 밝기로 복원했어요."}
            finally:
                service = monitor = parameters = result = None
                pythoncom.CoUninitialize()
