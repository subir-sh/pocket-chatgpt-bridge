"""Read local chat metadata through the documented app-server thread/list API."""
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
from .media import MediaStore


class ThreadCatalog:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.lines = None
        self.counter = 0
        self.listed = {}
        self.media = MediaStore()
        self.cwd = {}

    @staticmethod
    def binary():
        override = os.getenv("POCKET_CODEX_EXE")
        if override and Path(override).is_file():
            return override
        root = Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "OpenAI/Codex/bin"
        choices = list(root.glob("*/codex.exe")) if root.exists() else []
        if choices:
            return str(max(choices, key=lambda path: path.stat().st_mtime))
        found = shutil.which("codex.exe")
        if found:
            return found
        raise RuntimeError("최근 대화 목록을 읽는 ChatGPT 구성 요소를 찾지 못했어요. 앱을 업데이트해 주세요.")

    def close(self):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            self.process = None

    def _request(self, method, params):
        self.counter += 1
        request_id = self.counter
        self.process.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            try:
                value = self.lines.get(timeout=max(.1, deadline-time.monotonic()))
            except queue.Empty:
                break
            if value.get("id") == request_id:
                if "error" in value:
                    raise RuntimeError("ChatGPT가 대화 목록 요청을 처리하지 못했어요.")
                return value["result"]
        raise RuntimeError("최근 대화 목록 응답이 늦어요. 잠시 후 새로고침해 주세요.")

    def _start(self):
        if self.process and self.process.poll() is None:
            return
        self.lines = queue.Queue()
        self.process = subprocess.Popen([self.binary(), "app-server"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        process, lines = self.process, self.lines
        def read():
            for line in process.stdout:
                try:
                    lines.put(json.loads(line))
                except json.JSONDecodeError:
                    pass
        threading.Thread(target=read, name="chat-catalog", daemon=True).start()
        self._request("initialize", {"clientInfo": {"name": "pocket_chatgpt_bridge", "version": "0.6.0"}})
        self.process.stdin.write('{"method":"initialized"}\n')
        self.process.stdin.flush()

    def list(self, cursor=None):
        with self.lock:
            try:
                self._start()
                params = {"limit": 100, "sortKey": "updated_at", "sortDirection": "desc",
                          "sourceKinds": ["cli", "vscode", "appServer"], "archived": False, "useStateDbOnly": True}
                if cursor:
                    params["cursor"] = cursor
                result = self._request("thread/list", params)
                # Older app builds keep project membership in their local UI metadata.
                home = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex")))
                try:
                    metadata = json.loads((home / ".codex-global-state.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    metadata = {}
                assignments = metadata.get("thread-project-assignments", {})
                projects = metadata.get("local-projects", {})
                items = []
                for thread in result["data"]:
                    self.cwd[thread["id"]] = thread.get("cwd")
                    title = thread.get("name") or thread.get("preview", "").split("\n", 1)[0] or "제목 없는 대화"
                    project_id = thread.get("projectId") or assignments.get(thread["id"], {}).get("projectId")
                    project_name = projects.get(project_id, {}).get("name") if project_id else None
                    item = {"id": thread["id"], "title": title, "updated_at": thread.get("updatedAt"),
                            "project": bool(project_id), "project_name": project_name, "type": "local-chat"}
                    self.listed[item["id"]] = item
                    items.append(item)
                return {"items": items, "next_cursor": result.get("nextCursor"), "source": "app-server",
                        "verified": True, "note": "프로젝트 포함 · 최근 수정한 순서"}
            except Exception:
                self.close()
                raise

    def history(self, thread_id, cursor=None):
        """Read conversation history without resuming or generating a turn."""
        self.validate_id(thread_id)
        with self.lock:
            try:
                self._start()
                params = {"threadId": thread_id, "limit": 12, "sortDirection": "desc", "itemsView": "full"}
                if cursor:
                    params["cursor"] = cursor
                result = self._request("thread/turns/list", params)
                cwd = self.cwd.get(thread_id)
                if cwd is None:
                    details = self._request("thread/read", {"threadId": thread_id, "includeTurns": False})
                    cwd = details["thread"].get("cwd")
                    self.cwd[thread_id] = cwd
                messages = []
                turns = result.get("data", [])
                for turn in reversed(turns):
                    for item in turn.get("items", []):
                        kind = item.get("type")
                        if kind in ("imageGeneration", "imageView"):
                            ref = item.get("savedPath") or item.get("path")
                            if not ref and kind == "imageGeneration" and item.get("result"):
                                ref = "data:image/png;base64," + item["result"]
                            image = self.media.add(thread_id, ref, cwd=cwd,
                                alt="생성한 이미지" if kind == "imageGeneration" else "이미지")
                            if image:
                                messages.append({"id": turn["id"]+":"+item["id"], "role": "assistant", "text": "",
                                    "images": [image], "turn_id": turn["id"], "turn_status": turn.get("status")})
                            continue
                        if kind not in ("userMessage", "agentMessage"):
                            continue
                        attachments = []
                        images = []
                        if kind == "userMessage":
                            texts = []
                            for part in item.get("content", []):
                                if part.get("type") == "text":
                                    texts.append(part.get("text", ""))
                                elif part.get("type") in ("image", "localImage"):
                                    image = self.media.add(thread_id, part.get("url") or part.get("path"), cwd=cwd, alt="첨부 이미지")
                                    if image:
                                        images.append(image)
                                    else:
                                        attachments.append("이미지 위치를 확인하지 못했어요")
                                else:
                                    attachments.append(part.get("name") or "첨부 항목")
                            text = "\n\n".join(texts)
                        else:
                            text = item.get("text", "")
                            text, images = self.media.markdown(thread_id, text, cwd)
                        if text or attachments or images:
                            messages.append({"id": turn["id"] + ":" + item["id"],
                                "role": "user" if kind == "userMessage" else "assistant", "text": text,
                                "attachments": attachments, "images": images, "phase": item.get("phase"),
                                "turn_id": turn["id"], "turn_status": turn.get("status")})
                latest = turns[0] if turns else {}
                return {"messages": messages, "next_cursor": result.get("nextCursor"),
                        "latest_turn_id": latest.get("id"), "turn_status": latest.get("status"),
                        "error": (latest.get("error") or {}).get("message"), "source": "app-server"}
            except Exception:
                self.close()
                raise

    @staticmethod
    def validate_id(thread_id):
        if not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}", thread_id):
            raise RuntimeError("대화 ID가 올바르지 않아요. 목록을 새로고침해 주세요.")

    def open(self, thread_id):
        self.validate_id(thread_id)
        with self.lock:
            if thread_id not in self.listed:
                self._start()
                result = self._request("thread/read", {"threadId": thread_id, "includeTurns": False})
                thread = result["thread"]
                self.listed[thread_id] = {"title": thread.get("name") or "선택한 대화"}
            os.startfile("codex://threads/" + thread_id)
            return {"selected": self.listed[thread_id]["title"]}
