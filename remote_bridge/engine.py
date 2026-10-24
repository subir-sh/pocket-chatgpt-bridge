"""Windows UI Automation bridge. No ChatGPT API or private app storage access.

All actions are performed against the *foreground desktop window*. The bridge
never persists messages or session metadata; the desktop app owns those.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as WorkerTimeout
from dataclasses import dataclass
from typing import Any

from PIL import Image


class BridgeError(Exception):
    pass


@dataclass
class Config:
    window_re: str = os.getenv("CHATGPT_WINDOW_RE", r"ChatGPT")
    process_re: str = os.getenv("CHATGPT_PROCESS_RE", r"(?i)^ChatGPT(?:\.App| Desktop)?\.exe$")
    composer_x: float = float(os.getenv("REMOTE_COMPOSER_X", "0.50"))
    composer_y: float = float(os.getenv("REMOTE_COMPOSER_Y", "0.88"))
    allow_coordinate_input: bool = os.getenv("REMOTE_ALLOW_COORDINATE_INPUT", "0") == "1"
    local_hint_re: str = os.getenv("REMOTE_LOCAL_HINT_RE", r"(?i)local|로컬|this computer|이 컴퓨터|on this device")
    new_chat_re: str = os.getenv("REMOTE_NEW_CHAT_RE", r"(?i)new (chat|work|thread)|새 (채팅|대화)|new conversation")


# A *conservative* exclude list for static sidebar labels, not a model of the
# undocumented ChatGPT UI. Entries are candidates until confirmed on device.
EXCLUDE = re.compile(
    r"^(chatgpt|codex|chat|work|projects?|recent|history|settings|"
    r"new (chat|work|thread)|search|library|home|help|"
    r"새 (채팅|대화)|설정|검색|프로젝트|최근|기록|도움말)$", re.I
)


def _id(label: str, box: tuple[int, int, int, int]) -> str:
    return hashlib.sha256(f"{label}|{box}".encode("utf-8")).hexdigest()[:16]


class WindowsBridge:
    def __init__(self, config: Config | None = None):
        if sys.platform != "win32":
            raise RuntimeError("WindowsBridge requires Windows desktop")
        self.cfg = config or Config()
        self.lock = threading.RLock()
        # Delay imports so unit tests and web-only demo work on non-Windows hosts.
        import win32gui
        import win32con
        import win32process
        import psutil
        import pyautogui
        import pyperclip
        from PIL import ImageGrab
        self.win32gui = win32gui
        self.win32con = win32con
        self.win32process = win32process
        self.psutil = psutil
        self.pyautogui = pyautogui
        self.pyperclip = pyperclip
        self._desktop_local = threading.local()
        self._selected_handle = None
        from .catalog import ThreadCatalog
        self.catalog = ThreadCatalog()
        self._ui_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="desktop-uia")
        self.ImageGrab = ImageGrab
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.06
        if not 0 < self.cfg.composer_x < 1 or not 0 < self.cfg.composer_y < 1:
            raise ValueError("Composer coordinates must be normalized between 0 and 1")

    def list_windows(self):
        """Diagnostic inventory of candidate ChatGPT-titled windows (local CLI only)."""
        out = []
        for item in self.desktop.windows():
            try:
                label=item.window_text()
                if not re.search(self.cfg.window_re,label,re.I): continue
                _, pid=self.win32process.GetWindowThreadProcessId(item.handle)
                proc=self.psutil.Process(pid).name()
                out.append({"title":label,"process":proc,"pid":pid,"matches_process":bool(re.search(self.cfg.process_re,proc,re.I))})
            except Exception:pass
        return out

    @property
    def desktop(self):
        if not hasattr(self._desktop_local, "desktop"):
            # UIA rectangles are physical pixels. Keep mouse/capture Win32 calls
            # in that same coordinate space on mixed-scale secondary displays.
            import ctypes
            ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            import pythoncom
            pythoncom.CoInitialize()
            from pywinauto import Desktop
            self._desktop_local.desktop = Desktop(backend="uia")
        return self._desktop_local.desktop

    def _window_handle(self):
        # Health polling must not traverse UIA or wait behind a sidebar/capture operation.
        candidates = []
        def visit(handle, _):
            try:
                if not self.win32gui.IsWindowVisible(handle):
                    return
                _, pid = self.win32process.GetWindowThreadProcessId(handle)
                if not re.search(self.cfg.process_re, self.psutil.Process(pid).name(), re.I):
                    return
                left, top, right, bottom = self.win32gui.GetWindowRect(handle)
                if self.win32gui.IsIconic(handle) or (right-left >= 440 and bottom-top >= 360):
                    candidates.append(handle)
            except Exception:
                pass
        self.win32gui.EnumWindows(visit, None)
        if not candidates:
            raise BridgeError("ChatGPT 앱을 찾지 못했어요. 노트북에서 앱을 열어 주세요.")
        # Never silently choose a different conversation when multiple app windows exist.
        if len(candidates) > 1:
            foreground = self.win32gui.GetForegroundWindow()
            if self._selected_handle in candidates:
                return self._selected_handle
            if foreground in candidates:
                self._selected_handle = foreground
                return foreground
            raise BridgeError("여러 ChatGPT 창이 열려 있습니다. 노트북에서 조작할 창을 전면에 놓으세요.")
        self._selected_handle = candidates[0]
        return candidates[0]

    def _window(self):
        handle = self._window_handle()
        return self.desktop.window(handle=handle).wrapper_object()

    def activate(self):
        with self.lock:
            w=self._window()
            self._focus(w)
            return {"active": True}

    def _rect(self, window):
        r = window.rectangle()
        return (r.left, r.top, r.right, r.bottom)

    def _focus(self, window):
        if self.win32gui.IsIconic(window.handle):
            self.win32gui.ShowWindow(window.handle, self.win32con.SW_RESTORE)
        try:
            window.set_focus()
        except Exception as e:
            raise BridgeError(f"ChatGPT 창에 포커스를 줄 수 없습니다: {e}") from e
        time.sleep(0.12)
        if self.win32gui.GetForegroundWindow() != window.handle:
            raise BridgeError("ChatGPT 창이 전면에 있지 않습니다. 다른 창으로 오입력하지 않도록 중단했습니다.")

    def _all(self, window):
        try:
            return window.descendants()
        except Exception as e:
            raise BridgeError(f"UI Automation 검사 실패: {e}") from e

    def status(self) -> dict[str, Any]:
        try:
            handle = self._window_handle()
            return {"connected": True, "window": self.win32gui.GetWindowText(handle),
                    "foreground": self.win32gui.GetForegroundWindow()==handle,
                    "minimized": bool(self.win32gui.IsIconic(handle)),
                    "notice": "노트북 화면과 외부 모니터 어디에 있는 창이든 연결합니다."}
        except BridgeError as e:
            return {"connected": False, "window": None, "foreground": False, "notice": str(e)}

    def _candidate_controls(self, window):
        wl, wt, wr, wb = self._rect(window)
        ww, wh = wr-wl, wb-wt
        found = []
        seen = set()
        for control in window.descendants(control_type="ListItem"):
            try:
                typ = control.element_info.control_type
                if typ != "ListItem":
                    continue
                label = control.window_text().strip()
                if not label:
                    label = next((x.window_text().strip() for x in control.descendants(control_type="Text")
                                  if x.window_text().strip()), "")
                if not label or len(label) > 110 or EXCLUDE.fullmatch(label):
                    continue
                r = control.rectangle()
                if r.width() < 12 or r.height() < 10:
                    continue
                xmid = (r.left+r.right)/2
                ymid = (r.top+r.bottom)/2
                if not (wl+4 < xmid < wl+ww*.44 and wt+40 < ymid < wb-12):
                    continue
                pos = (round((xmid-wl)/ww, 3), round((ymid-wt)/wh, 3))
                sig = (label, round(pos[1], 2))
                if sig in seen:
                    continue
                seen.add(sig)
                found.append((control, {"id": _id(label, (int(pos[0]*1000),int(pos[1]*1000),len(label),0)),
                                        "title":label, "type":typ, "x":pos[0], "y":pos[1]}))
            except Exception:
                continue
        return found

    def sessions(self, cursor=None) -> dict[str, Any]:
        try:
            return self.catalog.list(cursor)
        except Exception as exc:
            raise BridgeError(str(exc)) from exc

    def submit_to(self, message, thread_id):
        self.select_session(thread_id)
        # Deep-link navigation may briefly remove the composer while loading.
        if self._wait_control(self._window(), "Edit", r".*", timeout=5) is None:
            raise BridgeError("대화 입력창을 불러오는 중이에요. 잠시 후 다시 시도해 주세요.")
        return self.submit(message)

    def history(self, thread_id, cursor=None):
        try:
            return self.catalog.history(thread_id, cursor)
        except Exception as exc:
            raise BridgeError(str(exc)) from exc

    def image(self, thread_id, asset_id):
        return self.catalog.media.read(thread_id, asset_id)

    def activity(self, since=None):
        try:
            return self.catalog.activity(since)
        except Exception as exc:
            raise BridgeError(str(exc)) from exc

    def close(self):
        self.catalog.close()
        self._ui_worker.shutdown(wait=False, cancel_futures=True)

    def dispatch(self, method, *args):
        try:
            return self._ui_worker.submit(getattr(self, method), *args).result(timeout=25)
        except WorkerTimeout as exc:
            raise BridgeError("ChatGPT 화면 작업이 지연되고 있어요. 화면에서 결과를 확인해 주세요.") from exc

    def select_session(self, session_id: str):
        with self.lock:
            try:
                result = self.catalog.open(session_id)
                time.sleep(.5)
                self._focus(self._window())
                return result
            except Exception as e:
                raise BridgeError(f"대화를 열지 못했어요: {e}") from e

    def _visible_local_mode_labels(self, w):
        """Look only in the app chrome, *not* in conversation text.

        This is a heuristic, not proof of whether ChatGPT will sync a new chat.
        The explicit mobile confirmation and actual desktop check remain required.
        """
        r = w.rectangle()
        labels = []
        for el in self._all(w):
            try:
                if not el.is_visible():
                    continue
                if el.element_info.control_type not in ("Text", "Button", "MenuItem"):
                    continue
                er = el.rectangle()
                xm = (er.left + er.right) / 2
                ym = (er.top + er.bottom) / 2
                left_chrome = xm < r.left + r.width() * 0.38
                upper_chrome = ym < r.top + r.height() * 0.18
                # Avoid accepting a mention of "local" from an actual message.
                if not (left_chrome or upper_chrome):
                    continue
                value = el.window_text().strip()
                if value:
                    labels.append(value)
            except Exception:
                pass
        return labels

    def new_session(self, confirmed: bool):
        if not confirmed:
            raise BridgeError("새 대화가 Local 모드인지 데스크톱에서 확인하고 승인해야 합니다.")
        with self.lock:
            w = self._window()
            self._focus(w)
            rect=w.rectangle()
            buttons=[]
            for el in w.descendants(control_type="Button"):
                try:
                    er=el.rectangle()
                    if (el.element_info.control_type=="Button" and el.is_visible()
                        and re.fullmatch(r"새 채팅|새 대화|New chat|New conversation",el.window_text().strip(),re.I)
                        and rect.top < er.top < rect.top+rect.height()*.3
                        and rect.left <= er.left < rect.left+rect.width()*.5):
                        buttons.append(el)
                except Exception:continue
            if not buttons:
                raise BridgeError("새 대화 버튼을 찾지 못했습니다. 화면에서 직접 열어 주세요.")
            self._invoke(buttons[0])
            location=self._wait_control(w,"Button",r"실행할 위치|where.*(?:run|work)|choose.*location")
            if location is None:
                raise BridgeError("새 입력창은 열었지만 실행 위치를 확인하지 못했습니다. 메시지를 보내기 전에 화면에서 Local을 선택하세요.")
            self._invoke(location)
            local=self._wait_control(w,"MenuItem",r"^(?:내 컴퓨터에서|이 컴퓨터에서|On this computer|This computer|Local\b)")
            if local is None:
                raise BridgeError("실행 위치 메뉴에서 로컬 항목을 찾지 못했습니다. 메시지를 보내지 않고 중단했습니다.")
            self._invoke(local)
            return {"created": True,"local_mode_requested":True,"note":"새 입력창에서 내 컴퓨터 실행을 선택했습니다. 첫 메시지를 보내면 대화가 생성됩니다."}

    @staticmethod
    def _invoke(control):
        try:control.invoke()
        except Exception:control.click_input()

    def _wait_control(self,w,control_type,pattern,timeout=5):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            for el in w.descendants(control_type=control_type):
                try:
                    if el.element_info.control_type==control_type and el.is_visible() and re.search(pattern,el.window_text().strip(),re.I):
                        return el
                except Exception:continue
            time.sleep(.15)
        return None

    def submit(self, message: str):
        if not message.strip():
            raise BridgeError("빈 메시지는 전송하지 않습니다.")
        if len(message) > 12000:
            raise BridgeError("한 번에 최대 12,000자까지 전송합니다.")
        with self.lock:
            w = self._window()
            self._focus(w)
            r=w.rectangle()
            edits=[]
            for el in w.descendants(control_type="Edit"):
                try:
                    if el.element_info.control_type != "Edit" or not el.is_visible(): continue
                    er=el.rectangle()
                    if er.width()<180 or er.height()<22 or er.top < r.top + r.height()*0.40: continue
                    label=el.window_text().lower()
                    score=3 if re.search(r"message|prompt|ask|instructions|질문|메시지|입력|보내", label, re.I) else 1
                    edits.append((score,er.top,el))
                except Exception:
                    continue
            if edits:
                edits.sort(key=lambda x:(x[0],x[1]),reverse=True)
                # Multiple unlabelled editors are ambiguous; use calibrated fallback.
                top=edits[0]
                if len(edits)==1 or top[0] > edits[1][0]:
                    try:
                        top[2].click_input()
                    except Exception as e:
                        raise BridgeError(f"입력창 포커스 실패: {e}") from e
                else:
                    self._composer_fallback(w)
            else:
                self._composer_fallback(w)
            if self.win32gui.GetForegroundWindow()!=w.handle:
                raise BridgeError("메시지 입력 직전 다른 창이 활성화되어 중단했습니다.")
            # Paste multilingual/large text in one operation. This temporarily
            # changes the system *text* clipboard; preserve and restore it.
            prior=None
            try:
                prior=self.pyperclip.paste()
            except Exception: pass
            try:
                self.pyperclip.copy(message)
                self.pyautogui.hotkey("ctrl","v")
                time.sleep(.18)
                # Enter may create a newline if desktop settings are configured
                # differently. Use only for configured Enter-to-send setups.
                self.pyautogui.press("enter")
            finally:
                if prior is not None:
                    time.sleep(.12)
                    try: self.pyperclip.copy(prior)
                    except Exception: pass
            return {"input_sent":True,"note":"키 입력을 전달했습니다. 서버는 실제 답변 생성 여부를 확인하지 않습니다."}

    def _composer_fallback(self,w):
        if not self.cfg.allow_coordinate_input:
            raise BridgeError("UIA 입력창을 확정하지 못했습니다. 화면에서 확인하거나 보정 후 REMOTE_ALLOW_COORDINATE_INPUT=1을 설정하세요.")
        self._click_relative(w,self.cfg.composer_x,self.cfg.composer_y)

    def _click_relative(self,w,x:float,y:float):
        if not (0<=x<=1 and 0<=y<=1):
            raise BridgeError("클릭 좌표는 0~1 범위여야 합니다.")
        r=w.rectangle()
        self.pyautogui.click(round(r.left+r.width()*x),round(r.top+r.height()*y))

    def click(self,x:float,y:float):
        with self.lock:
            w=self._window(); self._focus(w)
            self._click_relative(w,x,y)
            return {"clicked":True}

    def scroll(self,amount:int,sidebar:bool=False):
        if not -8<=amount<=8 or amount==0:
            raise BridgeError("스크롤 양은 -8~8 (0 제외)이어야 합니다.")
        with self.lock:
            w=self._window();self._focus(w)
            r=w.rectangle()
            x=r.left+r.width()*(.2 if sidebar else .7)
            y=r.top+r.height()*.55
            self.pyautogui.moveTo(int(x),int(y))
            self.pyautogui.scroll(amount)
            return {"scrolled":amount}

    def preview(self) -> bytes:
        with self.lock:
            w=self._window()
            if self.win32gui.IsIconic(w.handle):
                self._focus(w)
            # Capture the target HWND, including on secondary monitors or behind other apps.
            im=self.ImageGrab.grab(window=w.handle)
            if im.getbbox() is None:
                # GPU-backed Electron windows may not implement background PrintWindow.
                self._focus(w)
                im=self.ImageGrab.grab(bbox=self._rect(w),all_screens=True)
            im.thumbnail((1280,1000),Image.Resampling.LANCZOS)
            buf=io.BytesIO()
            im.convert("RGB").save(buf,format="JPEG",quality=71,optimize=True)
            return buf.getvalue()

    def inspect(self) -> list[dict[str,Any]]:
        with self.lock:
            w=self._window()
            out=[]
            for el in self._all(w):
                try:
                    rect=el.rectangle()
                    out.append({"type":el.element_info.control_type,"name":el.window_text()[:120],
                                "rect":[rect.left,rect.top,rect.right,rect.bottom]})
                except Exception:continue
            return out[:800]


class DemoBridge:
    """Opt-in smoke-test provider. Never sends data to ChatGPT."""
    def __init__(self):
        self.lock=threading.RLock()
        self.messages=[]
        self.reply_events=[]
        from .media import MediaStore
        self.media=MediaStore()
        self.current="Example local conversation"
        self.items=["Example local conversation","Another example"]
    def activate(self):return {"active":True}
    def status(self):
        return {"connected":True,"window":"DEMO — no ChatGPT connection","foreground":True,"notice":"Demo only; no real app is controlled."}
    def sessions(self,cursor=None):
        return {"items":[{"id":_id(n,(0,0,0,0)),"title":n,"type":"Demo"} for n in self.items],"source":"demo","verified":False,"note":"Demo sessions only"}
    def select_session(self,session_id):
        for n in self.items:
            if _id(n,(0,0,0,0))==session_id:
                self.current=n;return {"selected":n}
        raise BridgeError("Not found")
    def history(self, thread_id, cursor=None):
        if not any(_id(n,(0,0,0,0)) == thread_id for n in self.items):
            raise BridgeError("Not found")
        messages = [{"id": "demo-user", "role": "user", "text": "폰에서도 이전 대화를 편하게 읽을 수 있어?"},
                    {"id": "demo-answer", "role": "assistant", "text": "네. **대화 내용을 글자로** 읽을 수 있어요.\n\n- 이전 메시지 확인\n- 긴 답변과 코드 읽기\n- 같은 대화에 이어서 질문하기\n\n```python\nprint('Hello from Pocket')\n```\n\n| 기능 | 상태 |\n| --- | --- |\n| 대화 내역 | 표시됨 |\n| 자동 갱신 | 2초 간격 |"}]
        for i, event in enumerate(self.reply_events):
            if event["id"] != thread_id:
                continue
            text=event["text"]
            messages.extend([{"id": f"demo-u-{i}", "role": "user", "text": text},
                             {"id": f"demo-a-{i}", "role": "assistant", "text": "테스트 답변입니다. 실제 ChatGPT에는 전송하지 않았어요."}])
        import base64
        from PIL import ImageDraw
        sample=Image.new("RGB",(800,440),"#c8e1d3");draw=ImageDraw.Draw(sample)
        draw.rounded_rectangle((65,55,735,385),28,fill="#1e3028")
        draw.line([(125,315),(265,255),(400,280),(530,165),(675,115)],fill="#bce4ce",width=8)
        out=io.BytesIO();sample.save(out,"PNG")
        image=self.media.add(thread_id,"data:image/png;base64,"+base64.b64encode(out.getvalue()).decode(),alt="이미지 표시 예시")
        messages[1]["images"]=[image]
        latest=next((event for event in reversed(self.reply_events) if event["id"]==thread_id),None)
        return {"messages": messages, "next_cursor": None, "turn_status": "completed", "source": "demo",
                "reply_marker": latest["reply_marker"] if latest else "demo-initial", "reply_at": latest["reply_at"] if latest else 0}
    def activity(self, since=None):
        latest={event["id"]:event for event in self.reply_events if since is not None and event["reply_at"]>=since-2}
        return {"now": time.time(), "items": [{key:event[key] for key in ("id","reply_marker","reply_at")} for event in latest.values()]}
    def image(self, thread_id, asset_id):
        return self.media.read(thread_id, asset_id)
    def submit_to(self, message, thread_id):
        self.select_session(thread_id)
        return self.submit(message)
    def new_session(self,confirmed):
        if not confirmed:raise BridgeError("Confirm local session first")
        n=f"Example {len(self.items)+1}"
        self.items.append(n);self.current=n
        return {"created":True}
    def submit(self,message):
        if not message.strip():raise BridgeError("Empty message")
        self.messages.append(message)
        self.reply_events.append({"id":_id(self.current,(0,0,0,0)),"text":message,
                                  "reply_marker":str(time.time_ns()),"reply_at":time.time()})
        return {"input_sent":True,"note":"Demo only"}
    def click(self,x,y):return {"clicked":True}
    def scroll(self,amount,sidebar=False):return {"scrolled":amount}
    def preview(self):
        from PIL import ImageDraw
        im=Image.new("RGB",(820,560),"#141820");d=ImageDraw.Draw(im)
        d.text((32,30),"DEMO — no desktop connection",fill="white")
        d.text((32,70),f"Active: {self.current}",fill="white")
        for i,m in enumerate(self.messages[-5:]):d.text((32,140+i*55),m[:85],fill="white")
        out=io.BytesIO(); im.save(out,"JPEG");return out.getvalue()
    def inspect(self):return [{"type":"Demo","name":self.current,"rect":[0,0,10,10]}]
