"""Double-click Windows launcher. Also hosts the bundled server subprocess."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import webbrowser

PORT = 8765
DATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ChatGPTRemoteController"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Cancelled(Exception):
    pass


def command(args, **kwargs):
    environment = os.environ.copy()
    # A launcher started from PowerShell 7 can otherwise break Windows PowerShell's modules.
    if Path(args[0]).name.lower() == "powershell.exe":
        environment["PSModulePath"] = str(Path(environment.get("SystemRoot", r"C:\Windows")) /
                                           "System32/WindowsPowerShell/v1.0/Modules")
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", creationflags=NO_WINDOW, env=environment, **kwargs)


def tailscale_path():
    candidates = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale/tailscale.exe"]
    found = shutil.which("tailscale")
    if found:
        candidates.append(Path(found))
    return next((str(p) for p in candidates if p.is_file()), None)


def download_installer(destination):
    # Discover the current official release; do not execute an unsigned download.
    with urllib.request.urlopen("https://pkgs.tailscale.com/stable/?mode=json", timeout=30) as response:
        release = json.load(response)
    arch = "arm64" if os.environ.get("PROCESSOR_ARCHITECTURE", "").upper() == "ARM64" else "amd64"
    filename = release["MSIs"][arch]
    if not re.fullmatch(r"tailscale-setup-[\d.]+-(?:amd64|arm64)\.msi", filename):
        raise RuntimeError("Tailscale 설치 파일 정보를 확인하지 못했어요.")
    with urllib.request.urlopen("https://pkgs.tailscale.com/stable/" + filename, timeout=120) as response:
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output)
    path_literal = str(destination).replace("'", "''")
    verified = command(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        "$s = Get-AuthenticodeSignature -LiteralPath '" + path_literal + "'; "
        "if ($s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -match '(?i)(^|, )O=Tailscale Inc[.](,|$)') "
        "{ exit 0 } else { exit 1 }"], timeout=45)
    if verified.returncode:
        raise RuntimeError("Tailscale 공식 설치 파일의 서명을 확인하지 못해 설치를 중단했어요.")


def install_msi(path):
    # Only the installer is elevated. Desktop input continues at normal user level.
    class SHELLEXECUTEINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("fMask", ctypes.c_ulong),
                    ("hwnd", ctypes.c_void_p), ("lpVerb", ctypes.c_wchar_p),
                    ("lpFile", ctypes.c_wchar_p), ("lpParameters", ctypes.c_wchar_p),
                    ("lpDirectory", ctypes.c_wchar_p), ("nShow", ctypes.c_int),
                    ("hInstApp", ctypes.c_void_p), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
                    ("dwHotKey", ctypes.c_ulong), ("hIcon", ctypes.c_void_p),
                    ("hProcess", ctypes.c_void_p)]
    from ctypes import wintypes
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFO)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    info = SHELLEXECUTEINFO()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = "msiexec.exe"
    info.lpParameters = subprocess.list2cmdline(["/i", str(path), "/passive", "/norestart"])
    info.nShow = 0
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        raise RuntimeError("설치가 취소됐어요. 다시 시작한 뒤 Windows 설치 승인을 허용해 주세요.")
    try:
        kernel.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        result = wintypes.DWORD()
        kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(result))
        if result.value not in (0, 3010):
            raise RuntimeError(f"Tailscale 설치가 끝나지 않았어요. 설치 오류: {result.value}")
    finally:
        kernel.CloseHandle(info.hProcess)


def run_server():
    import argparse
    import logging
    import uvicorn
    from remote_bridge.app import create_app, default_token_file, load_or_create_token
    from remote_bridge.engine import DemoBridge, WindowsBridge
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge-server", action="store_true")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--token-file", type=Path, default=default_token_file())
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    token = os.environ.pop("POCKET_BRIDGE_PAIR_TOKEN", None) or load_or_create_token(args.token_file)
    app = create_app(DemoBridge() if args.demo else WindowsBridge(), token, prevent_sleep=not args.demo)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_config=None, access_log=False)


class Setup:
    def __init__(self, emit):
        self.emit = emit
        self.cancelled = threading.Event()
        self.active = None
        self.server = None
        self.server_job = None
        self.log = None
        self.token = ""
        self.opened_urls = set()

    def wait(self, seconds):
        if self.cancelled.wait(seconds):
            raise Cancelled()

    def stop(self):
        self.cancelled.set()
        if self.server_job:
            # Kill the onefile bootloader AND its server child when the launcher exits.
            self.server_job.Close()
            self.server_job = None
        for process in (self.active, self.server):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        if self.log:
            self.log.close()

    def state(self, text):
        if self.cancelled.is_set():
            raise Cancelled()
        self.emit("status", text)

    def interactive_command(self, args, timeout=180):
        # CLI output is consumed live so first-run login/HTTPS consent opens immediately.
        lines = queue.Queue()
        output = []
        offered_consent = False
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
            creationflags=NO_WINDOW)
        self.active = process

        def read():
            for line in process.stdout:
                lines.put(line)
            lines.put(None)

        threading.Thread(target=read, daemon=True).start()
        deadline = time.monotonic() + timeout
        done = False
        try:
            while not done:
                self.wait(0.1)
                if time.monotonic() > deadline:
                    raise RuntimeError("연결 승인을 기다리는 시간이 지났어요. 로그인 후 다시 시작해 주세요.")
                try:
                    line = lines.get_nowait()
                except queue.Empty:
                    continue
                if line is None:
                    done = True
                    continue
                output.append(line)
                for url in re.findall(r'https://login\.tailscale\.com/[^\s"<>]+', line):
                    offered_consent = True
                    if url not in self.opened_urls:
                        self.opened_urls.add(url)
                        self.state("열린 브라우저에서 Tailscale 로그인 또는 연결 승인을 완료해 주세요.")
                        webbrowser.open(url)
            process.wait(timeout=10)
            return process.returncode, "".join(output), offered_consent
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            process.stdout.close()
            self.active = None

    def start_chatgpt(self):
        import psutil
        if any(p.info["name"].lower() == "chatgpt.exe"
               for p in psutil.process_iter(["name"]) if p.info["name"]):
            return
        self.state("ChatGPT 앱을 열고 있어요…")
        result = command(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "Get-StartApps | Where-Object { $_.Name -eq 'ChatGPT' } | "
            "Select-Object -First 1 -ExpandProperty AppID"], timeout=30)
        app_id = result.stdout.strip()
        if not app_id:
            raise RuntimeError("ChatGPT 데스크톱 앱을 먼저 설치하고 로그인해 주세요.")
        os.startfile("shell:AppsFolder\\" + app_id)
        self.wait(5)

    def start_server(self):
        from remote_bridge.app import default_token_file, load_or_create_token
        self.state("원격 조작 서버를 켜고 있어요…")
        self.token = load_or_create_token(default_token_file())
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", PORT)) == 0:
                raise RuntimeError("이미 서버가 실행 중이거나 8765 포트를 사용 중이에요. 기존 실행 창을 종료해 주세요.")
        DATA.mkdir(parents=True, exist_ok=True)
        self.log = (DATA / "desktop-server.log").open("w", encoding="utf-8")
        import win32job
        self.server_job = win32job.CreateJobObject(None, "")
        limits = win32job.QueryInformationJobObject(self.server_job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(self.server_job, win32job.JobObjectExtendedLimitInformation, limits)
        args = [sys.executable]
        if not getattr(sys, "frozen", False):
            args.append(str(Path(__file__).resolve()))
        server_environment = os.environ.copy()
        server_environment["POCKET_BRIDGE_PAIR_TOKEN"] = self.token
        self.server = subprocess.Popen(args + ["--bridge-server", "--token-file", str(default_token_file())], stdout=self.log, stderr=self.log,
                                       creationflags=NO_WINDOW, env=server_environment)
        import win32api
        handle = win32api.OpenProcess(0x0100 | 0x0001, False, self.server.pid)
        try:
            win32job.AssignProcessToJobObject(self.server_job, handle)
        finally:
            handle.Close()
        for _ in range(90):
            self.wait(0.5)
            if self.server.poll() is not None:
                raise RuntimeError("서버를 시작하지 못했어요. '오류 기록 열기'에서 원인을 확인해 주세요.")
            try:
                ready = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/health",
                                               headers={"X-Remote-Token": self.token})
                with urllib.request.urlopen(ready, timeout=2) as response:
                    if response.status == 200:
                        return
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    raise RuntimeError("서버와 연결 키가 일치하지 않아 시작을 중단했어요. 기존 실행 창을 닫고 다시 시작해 주세요.") from exc
            except OSError:
                pass
        raise RuntimeError("서버 실행이 지연되고 있어요. 다시 시작해 주세요.")

    def configure_tailscale(self):
        ts = tailscale_path()
        if not ts:
            self.state("Tailscale을 내려받고 있어요. 이어서 나오는 Windows 설치 승인을 허용해 주세요.")
            DATA.mkdir(parents=True, exist_ok=True)
            installer = DATA / "tailscale-setup.msi"
            download_installer(installer)
            self.state("Tailscale 설치 중이에요. Windows 승인 창이 뜨면 허용해 주세요.")
            install_msi(installer)
            ts = tailscale_path()
            if not ts:
                raise RuntimeError("Tailscale 설치 후 실행 파일을 찾지 못했어요. Windows를 재시작한 뒤 다시 실행해 주세요.")
        self.state("Tailscale 연결을 확인하고 있어요…")
        state = {}
        for _ in range(20):
            result = command([ts, "status", "--json"], timeout=10)
            if result.returncode == 0:
                state = json.loads(result.stdout)
                break
            self.wait(1)
        if state.get("BackendState") != "Running":
            self.state("Tailscale에 연결하고 있어요. 로그인 화면이 열리면 로그인해 주세요.")
            code, output, _ = self.interactive_command([ts, "up", "--json", "--timeout=5m"], timeout=330)
            if code:
                raise RuntimeError("Tailscale 연결에 실패했어요. Tailscale 앱에서 로그인 상태를 확인한 뒤 다시 시작해 주세요.")
        self.state("폰에서 접속할 HTTPS 주소를 설정하고 있어요…")
        served = command([ts, "serve", "status", "--json"], timeout=15)
        if served.returncode:
            raise RuntimeError("Tailscale 웹 연결 상태를 확인하지 못했어요. 다시 시작해 주세요.")
        config = json.loads(served.stdout) or {}
        if any(config.get("AllowFunnel", {}).values()):
            raise RuntimeError("이 노트북에 공개 Funnel 설정이 있어 중단했어요. Tailscale에서 공개 설정을 해제해 주세요.")
        for site in config.get("Web", {}).values():
            handler = site.get("Handlers", {}).get("/", {})
            if handler and handler.get("Proxy") not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
                raise RuntimeError("Tailscale에서 다른 웹 서비스를 사용 중이에요. 기존 연결 설정을 먼저 확인해 주세요.")
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            code, output, consent = self.interactive_command(
                [ts, "serve", "--bg", "--yes", str(PORT)], timeout=330)
            if code == 0:
                break
            if not consent:
                raise RuntimeError("Tailscale 웹 연결 설정에 실패했어요. Tailscale 연결 상태를 확인해 주세요.")
            self.wait(5)
        else:
            raise RuntimeError("브라우저에서 HTTPS 연결 승인을 완료한 뒤 다시 시작해 주세요.")
        result = command([ts, "status", "--json"], timeout=15)
        state = json.loads(result.stdout)
        dns = state.get("Self", {}).get("DNSName", "").rstrip(".")
        if not dns.endswith(".ts.net"):
            raise RuntimeError("폰 접속 주소를 찾지 못했어요. Tailscale의 MagicDNS 설정을 확인해 주세요.")
        # A preexisting public Funnel route must never be reused for desktop control.
        served = command([ts, "serve", "status", "--json"], timeout=15)
        config = json.loads(served.stdout) or {}
        if any(config.get("AllowFunnel", {}).values()):
            raise RuntimeError("이 노트북에 공개 Funnel 설정이 있어 중단했어요. Tailscale에서 공개 설정을 해제해 주세요.")
        return "https://" + dns

    def run(self):
        try:
            self.start_chatgpt()
            self.start_server()
            url = self.configure_tailscale()
            self.emit("ready", (url, self.token))
            while not self.cancelled.wait(2):
                if self.server.poll() is not None:
                    raise RuntimeError("서버가 종료됐어요. '오류 기록 열기'를 확인하고 다시 시작해 주세요.")
        except Cancelled:
            pass
        except Exception as exc:
            self.stop()
            self.emit("error", str(exc))


class Launcher:
    def __init__(self, root, *, autostart=True):
        import tkinter as tk
        from tkinter import ttk
        self.root = root
        self.events = queue.Queue()
        self.setup = None
        self.qr_image = None
        self.connection_url = ""
        root.title("Pocket ChatGPT Bridge · v0.6.0")
        root.geometry("680x800")
        root.minsize(620, 760)
        root.configure(bg="#f5f6f8")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#f5f6f8")
        style.configure("TLabel", background="#f5f6f8", font=("맑은 고딕", 10))
        style.configure("TButton", font=("맑은 고딕", 10), padding=(12, 7))
        frame = ttk.Frame(root, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Pocket ChatGPT Bridge", font=("맑은 고딕", 21, "bold")).pack(anchor="w")
        ttk.Label(frame, text="이 창을 켜두면 밖에서도 아이폰으로 ChatGPT를 쓸 수 있어요.").pack(anchor="w", pady=(6, 16))
        self.status = tk.StringVar(value="시작 준비 중…")
        ttk.Label(frame, textvariable=self.status, wraplength=600,
                  font=("맑은 고딕", 11, "bold")).pack(anchor="w", pady=(0, 12))
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 18))
        ttk.Label(frame, text="처음 한 번만 · 아이폰 준비", font=("맑은 고딕", 11, "bold")).pack(anchor="w")
        ttk.Label(frame, text="아이폰 App Store에서 Tailscale 설치 → 노트북에서 쓴 Tailscale 계정으로\n"
                  "로그인 → 연결 켜기. ChatGPT 계정을 바꾸는 과정은 아니에요.",
                  wraplength=600).pack(anchor="w", pady=(6, 20))
        ttk.Label(frame, text="아이폰의 기본 카메라로 아래 QR 코드를 비추세요", font=("맑은 고딕", 12, "bold")).pack(anchor="w")
        ttk.Label(frame, text="카메라에 뜨는 링크를 누르면 바로 연결돼요. 연결 키를 입력할 필요 없어요.",
                  wraplength=600).pack(anchor="w", pady=(6, 0))
        self.qr = ttk.Label(frame, text="연결 준비가 끝나면 QR 코드가 표시됩니다.", anchor="center")
        self.qr.pack(pady=14)
        ttk.Label(frame, text="다음부터는 Safari 즐겨찾기로 열면 돼요. 이 폰의 연결은 자동으로 기억해요.",
                  wraplength=600).pack(anchor="w", pady=(0, 12))
        self.details = ttk.Frame(frame)
        self.details_button = ttk.Button(frame, text="QR 대신 링크로 연결 / 고급 정보", command=self.toggle_details)
        self.details_button.pack(anchor="w", pady=(0, 10))
        self.url = tk.StringVar()
        ttk.Entry(self.details, textvariable=self.url, state="readonly").pack(fill="x", pady=(0, 6))
        actions = ttk.Frame(self.details)
        actions.pack(fill="x")
        self.copy_url = ttk.Button(actions, text="자동 연결 링크 복사", command=lambda: self.copy(self.connection_url), state="disabled")
        self.copy_url.pack(side="left")
        self.open_url = ttk.Button(actions, text="노트북에서 확인", command=lambda: webbrowser.open(self.connection_url), state="disabled")
        self.open_url.pack(side="left", padx=8)
        ttk.Label(self.details, text="직접 입력용 연결 키 (QR을 사용하면 필요 없어요)").pack(anchor="w", pady=(10, 0))
        self.token = tk.StringVar()
        token_row = ttk.Frame(self.details)
        token_row.pack(fill="x", pady=(8, 12))
        self.token_entry = ttk.Entry(token_row, textvariable=self.token, state="readonly", show="•")
        self.token_entry.pack(side="left", fill="x", expand=True)
        self.copy_token = ttk.Button(token_row, text="연결 키 복사", command=lambda: self.copy(self.token.get()), state="disabled")
        self.copy_token.pack(side="left", padx=(8, 0))
        self.show_token = tk.BooleanVar()
        ttk.Checkbutton(self.details, text="연결 키 표시", variable=self.show_token,
                        command=lambda: self.token_entry.configure(show="" if self.show_token.get() else "•")).pack(anchor="w")
        ttk.Label(frame, text="외출할 때: 이 창 켜두기 · 전원 연결 · 덮개 열림 · Windows 잠금 해제",
                  wraplength=600).pack(anchor="w", pady=(16, 12))
        bottom = ttk.Frame(frame)
        bottom.pack(fill="x", side="bottom", before=frame.winfo_children()[0])
        self.retry = ttk.Button(bottom, text="다시 시작", command=self.start, state="disabled")
        self.retry.pack(side="left")
        ttk.Button(bottom, text="오류 기록 열기", command=self.open_log).pack(side="left", padx=8)
        ttk.Button(bottom, text="종료", command=self.close).pack(side="right")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.drain)
        if autostart:
            root.after(250, self.start)

    def copy(self, value):
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.root.update_idletasks()

    def toggle_details(self):
        if self.details.winfo_manager():
            self.details.pack_forget()
            self.root.geometry("680x800")
        else:
            self.details.pack(fill="x", after=self.details_button)
            self.root.geometry("680x1000")

    def open_log(self):
        log = DATA / "desktop-server.log"
        if log.exists():
            os.startfile(str(log))

    def start(self):
        if self.setup:
            self.setup.stop()
        self.url.set("")
        self.token.set("")
        self.connection_url = ""
        self.qr.configure(image="", text="연결 준비 중…")
        for button in (self.copy_url, self.open_url, self.copy_token, self.retry):
            button.configure(state="disabled")
        self.progress.start(12)
        self.status.set("시작 준비 중…")
        self.setup = Setup(lambda kind, value: self.events.put((kind, value)))
        threading.Thread(target=self.setup.run, daemon=True).start()

    def drain(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.status.set(value)
                elif kind == "error":
                    self.progress.stop()
                    self.status.set(value)
                    self.retry.configure(state="normal")
                elif kind == "ready":
                    import qrcode
                    from PIL import ImageTk
                    self.progress.stop()
                    self.status.set("준비 완료 · 이제 아이폰 카메라로 아래 QR을 스캔하세요")
                    self.url.set(value[0])
                    self.token.set(value[1])
                    self.connection_url = value[0] + "/?v=0.6.0#connect=" + value[1]
                    qr = qrcode.QRCode(box_size=4, border=3)
                    qr.add_data(self.connection_url)
                    qr.make(fit=True)
                    image = qr.make_image().convert("RGB")
                    self.qr_image = ImageTk.PhotoImage(image)
                    self.qr.configure(image=self.qr_image, text="")
                    for button in (self.copy_url, self.open_url, self.copy_token):
                        button.configure(state="normal")
        except queue.Empty:
            pass
        self.root.after(100, self.drain)

    def close(self):
        if self.setup:
            self.setup.stop()
        self.root.destroy()


def main():
    # windowed PyInstaller programs have no stdout/stderr; always supply real handles.
    DATA.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = (DATA / "launcher-errors.log").open("a", encoding="utf-8")
    if "--bridge-server" in sys.argv:
        run_server()
        return
    import tkinter as tk
    root = tk.Tk()
    ui_check = "--check-ui" in sys.argv
    launcher = Launcher(root, autostart=not ui_check)
    if ui_check:
        # Packaging smoke check: create the real UI without installing or connecting.
        root.after(2000, launcher.close)
    root.mainloop()


if __name__ == "__main__":
    main()
