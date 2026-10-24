"""Dim supported panels and disable unsupported external outputs when requested."""
import hashlib
import json
import os
from pathlib import Path
import threading
from .outputs import DisplayOutputs


class BrightnessControl:
    def __init__(self):
        self.lock = threading.Lock()
        self.saved_file = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "ChatGPTRemoteController/brightness.json"
        self.outputs = DisplayOutputs()

    @staticmethod
    def key(monitor):
        identity = monitor.get("serial") or monitor.get("edid") or (monitor.get("name"), monitor["index"])
        return monitor["method"].__name__ + ":" + hashlib.sha256(str(identity).encode()).hexdigest()[:24]

    @staticmethod
    def read(monitor):
        options = {"display": monitor["index"]}
        if monitor["method"].__name__ == "VCP":
            options["max_tries"] = 3
        return next((int(x) for x in monitor["method"].get_brightness(**options) if x is not None), None)

    @staticmethod
    def write(monitor, value):
        options = {"display": monitor["index"]}
        if monitor["method"].__name__ == "VCP":
            options["max_tries"] = 3
        monitor["method"].set_brightness(int(value), **options)

    def set_dimmed(self, dimmed):
        import pythoncom
        import screen_brightness_control as sbc
        import win32api
        with self.lock:
            pythoncom.CoInitialize()
            try:
                if not dimmed:
                    if self.outputs.saved_file.exists():
                        self.outputs.set_external(True)
                saved = json.loads(self.saved_file.read_text(encoding="utf-8")) if self.saved_file.exists() else {}
                screens = len(win32api.EnumDisplayMonitors())
                try:
                    monitors = sbc.list_monitors_info()
                except Exception:
                    monitors = []
                changed = 0
                errors = []
                for monitor in monitors:
                    key = self.key(monitor)
                    try:
                        if dimmed:
                            level = self.read(monitor)
                            if level is None:
                                continue
                            saved.setdefault(key, level)
                            self.saved_file.parent.mkdir(parents=True, exist_ok=True)
                            self.saved_file.write_text(json.dumps(saved), encoding="utf-8")
                            self.write(monitor, 0)
                        elif key in saved:
                            self.write(monitor, saved[key])
                            saved.pop(key)
                        else:
                            continue
                        changed += 1
                    except Exception:
                        errors.append(monitor.get("name", "모니터"))
                if dimmed and changed < screens:
                    self.outputs.set_external(False)
                    note = "지원 화면의 밝기를 낮추고, 외부 모니터 출력을 껐어요."
                elif dimmed:
                    note = f"{changed}개 화면의 밝기를 최소로 낮췄어요."
                else:
                    if saved:
                        self.saved_file.write_text(json.dumps(saved), encoding="utf-8")
                    else:
                        self.saved_file.unlink(missing_ok=True)
                    note = "화면 출력과 이전 밝기를 복원했어요."
                    if errors:
                        note += " 일부 화면의 밝기 복원에 실패했어요. 다시 눌러 주세요."
                return {"dimmed": dimmed, "screens": screens, "hardware_changed": changed,
                        "note": note}
            finally:
                pythoncom.CoUninitialize()
