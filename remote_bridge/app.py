"""Authenticated localhost FastAPI bridge for iPhone PWA -> Windows ChatGPT UI."""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import os
from pathlib import Path
import secrets
import threading
from typing import Any
from importlib.resources import files

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import BridgeError, DemoBridge, WindowsBridge
from .power import SleepGuard
from .display import BrightnessControl


def default_token_file() -> Path:
    base = Path(os.getenv("LOCALAPPDATA", str(Path.home() / ".local" / "share")))
    return base / "ChatGPTRemoteController" / "pairing-token.txt"


def load_or_create_token(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        key = path.read_text(encoding="utf-8").strip()
        if len(key) < 32:
            raise ValueError("Existing pairing token is too short; delete it to regenerate")
        return key
    token = secrets.token_urlsafe(36)
    # Exclusive creation prevents accidental overwrites or races.
    try:
        with path.open("x", encoding="utf-8") as f:
            f.write(token)
        try: os.chmod(path,0o600)
        except OSError: pass
    except FileExistsError:
        return load_or_create_token(path)
    return token


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    request_id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    server_instance: str | None = Field(default=None, max_length=100)


class SessionSelection(BaseModel):
    id: str = Field(min_length=1,max_length=80)


class NewSession(BaseModel):
    confirmed_local: bool = False


class Click(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class Scroll(BaseModel):
    amount: int = Field(ge=-8,le=8)
    sidebar: bool = False


class BrightnessRequest(BaseModel):
    dimmed: bool


class DisplayOutputRequest(BaseModel):
    enabled: bool


def create_app(driver=None, token: str | None = None, *, prevent_sleep: bool = False, sleep_guard_factory=SleepGuard) -> FastAPI:
    token = token or load_or_create_token(default_token_file())
    driver = driver if driver is not None else WindowsBridge()
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        guard = sleep_guard_factory() if prevent_sleep else None
        if guard is not None:
            guard.start()
        app.state.sleep_guard = guard
        try:
            yield
        finally:
            if guard is not None:
                guard.stop()
            if hasattr(driver, "close"):
                driver.close()

    app = FastAPI(title="Pocket ChatGPT Bridge", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.driver = driver
    brightness = BrightnessControl()
    def desktop_call(method, *args):
        if hasattr(driver, "dispatch"):
            return driver.dispatch(method, *args)
        return getattr(driver, method)(*args)
    server_instance = secrets.token_urlsafe(18)
    # Idempotent send requests reduce accidental duplicated messages on retry.
    sent: dict[str, dict[str, Any]] = {}
    sent_lock = threading.Lock()

    def auth(x_remote_token: str | None=Header(None)):
        if not x_remote_token or not secrets.compare_digest(x_remote_token,token):
            raise HTTPException(status_code=401,detail="Invalid pairing token")
        return True

    @app.middleware("http")
    async def hardened(request: Request, call_next):
        resp=await call_next(request)
        resp.headers["Cache-Control"]="no-store"
        resp.headers["X-Content-Type-Options"]="nosniff"
        resp.headers["Referrer-Policy"]="no-referrer"
        resp.headers["X-Frame-Options"]="DENY"
        resp.headers["Content-Security-Policy"]="default-src 'self'; connect-src 'self'; img-src 'self' blob: data:; script-src 'self'; style-src 'self'; manifest-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'"
        return resp

    @app.exception_handler(BridgeError)
    async def bridge_err(_, exc: BridgeError):
        return Response(content=__import__("json").dumps({"detail":str(exc)},ensure_ascii=False),
                        media_type="application/json",status_code=409)

    @app.get("/api/status", dependencies=[Depends(auth)])
    def status():
        details = driver.status()
        guard = getattr(app.state, "sleep_guard", None)
        return {**details, "sleep_prevention_active": bool(guard and guard.active), "server_instance": server_instance}

    @app.get("/api/health", dependencies=[Depends(auth)])
    def health():
        return {"ready": True, "server_instance": server_instance}

    @app.post("/api/activate", dependencies=[Depends(auth)])
    def activate(): return desktop_call("activate")

    @app.get("/api/sessions", dependencies=[Depends(auth)])
    def sessions(cursor: str | None = None): return driver.sessions(cursor)

    @app.post("/api/sessions/select", dependencies=[Depends(auth)])
    def select(req: SessionSelection): return desktop_call("select_session", req.id)

    @app.post("/api/sessions/new", dependencies=[Depends(auth)])
    def new(req: NewSession): return desktop_call("new_session", req.confirmed_local)

    @app.post("/api/send", dependencies=[Depends(auth)])
    def send(req: Message):
        if req.server_instance is not None and req.server_instance != server_instance:
            raise HTTPException(409,"브리지가 재시작되어 이전 전송 결과를 확인할 수 없습니다. 화면에서 확인 후 새로 작성하세요.")
        with sent_lock:
            if req.request_id in sent:
                result = sent[req.request_id]
                if result["text"] != req.text:
                    raise HTTPException(409,"Idempotency key reused for different message")
                return result["response"]
            # Mark the request as uncertain BEFORE triggering the UI, so a
            # network retry cannot blindly send the same message twice.
            sent[req.request_id]={"text":req.text,"state":"pending","response":{"input_sent":False,"note":"이미 접수한 전송입니다. 화면에서 결과를 확인하세요."}}
            # Never forget an ID and then accept it as a new request.
            if len(sent)>2000:
                sent.pop(req.request_id)
                raise HTTPException(503,"전송 기록 한도입니다. 미확인 전송을 확인한 뒤 브리지를 재시작하세요.")
        try:
            result=desktop_call("submit",req.text)
        except Exception:
            with sent_lock:sent[req.request_id]["state"]="uncertain"
            raise
        with sent_lock:
            sent[req.request_id]["response"]=result
            sent[req.request_id]["state"]="completed"
        return result

    @app.get("/api/sends/{request_id}", dependencies=[Depends(auth)])
    def send_status(request_id: str):
        with sent_lock:
            if request_id not in sent:
                raise HTTPException(404,"전송 기록이 없습니다.")
            record=sent[request_id]
            return {"state":record["state"],"server_instance":server_instance,**record["response"]}

    @app.get("/api/preview",dependencies=[Depends(auth)])
    def preview():return Response(desktop_call("preview"),media_type="image/jpeg")

    @app.post("/api/click",dependencies=[Depends(auth)])
    def click(req:Click):return desktop_call("click",req.x,req.y)

    @app.post("/api/scroll",dependencies=[Depends(auth)])
    def scroll(req:Scroll):return desktop_call("scroll",req.amount,req.sidebar)

    @app.post("/api/brightness", dependencies=[Depends(auth)])
    def change_brightness(req: BrightnessRequest):
        if isinstance(driver, DemoBridge):
            return {"dimmed": False, "note": "미리보기 모드에서는 실제 밝기를 바꾸지 않아요."}
        try:
            return brightness.set_dimmed(req.dimmed)
        except Exception as exc:
            raise HTTPException(409, "화면을 조절하지 못했어요. Windows 화면 설정을 확인해 주세요.") from exc

    @app.post("/api/display-output", dependencies=[Depends(auth)])
    def display_output(req: DisplayOutputRequest):
        if isinstance(driver, DemoBridge):
            return {"external_enabled": req.enabled, "note": "미리보기 모드에서는 화면 출력을 바꾸지 않아요."}
        try:
            return brightness.outputs.set_external(req.enabled)
        except Exception as exc:
            raise HTTPException(409, "외부 화면 출력을 변경하지 못했어요. 노트북 화면이 사용 가능한지 확인해 주세요.") from exc

    static_path = Path(__file__).with_name("static")
    @app.get("/")
    def index():return FileResponse(static_path / "index.html")
    app.mount("/assets",StaticFiles(directory=str(static_path)),name="static")
    return app


def main():
    parser=argparse.ArgumentParser(description="Authenticated Windows ChatGPT remote-control bridge")
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--allow-sleep", action="store_true", help="Do not block Windows idle sleep while the bridge runs")
    parser.add_argument("--demo",action="store_true",help="Run without controlling the real ChatGPT app")
    parser.add_argument("--inspect",action="store_true",help="Print current ChatGPT UI Automation controls locally")
    parser.add_argument("--list-windows",action="store_true",help="Print ChatGPT-titled window process names to calibrate selection")
    parser.add_argument("--token-file",type=Path,default=default_token_file())
    args=parser.parse_args()
    if args.demo and (args.inspect or args.list_windows):parser.error("--demo cannot be used with diagnostic options")
    if args.list_windows:
        import json
        print(json.dumps(WindowsBridge().list_windows(),ensure_ascii=False,indent=2))
        return
    if args.inspect:
        import json
        print(json.dumps(WindowsBridge().inspect(),ensure_ascii=False,indent=2))
        return
    if not (1<=args.port<=65535):parser.error("Port must be in range 1..65535")
    token=load_or_create_token(args.token_file)
    bridge=DemoBridge() if args.demo else WindowsBridge()
    print("\nPocket ChatGPT Bridge")
    print("Local URL: http://127.0.0.1:%s"%args.port)
    print("Pairing token (keep private):",token)
    print("Access from iPhone through Tailscale Serve HTTPS; NEVER expose port to the public internet.\n")
    import uvicorn
    prevent_sleep = (not args.demo) and (not args.allow_sleep)
    if prevent_sleep:
        print("Windows idle sleep: BLOCKED while bridge runs; screen timeout remains enabled.")
        print("Closing the lid, explicit sleep, or Windows locking are NOT overridden.")
    uvicorn.run(create_app(bridge,token,prevent_sleep=prevent_sleep),host="127.0.0.1",port=args.port,log_level="info")

if __name__=="__main__":main()
