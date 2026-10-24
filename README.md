# Pocket ChatGPT Bridge

Use your iPhone to control the Windows ChatGPT app left running at home. Your phone's official ChatGPT account stays separate. Prompts enter the desktop conversation and responses appear in a window preview. No paid model API is called. Local chat metadata is read through the documented app-server API.

**[한국어 설명서](README.ko.md)** · [Actual verification and limits](TESTING.md)

## Setup

Windows 10/11 and a signed-in ChatGPT Desktop app are required. The standalone **PocketChatGPTBridge.exe** includes Python and its dependencies.

1. Connect the laptop to power, leave the lid open and Windows unlocked, and open the desired local Work conversation.
2. Double-click `PocketChatGPTBridge.exe`. It opens ChatGPT if needed, starts the bridge and sleep guard, installs Tailscale if missing, connects it, and configures private HTTPS.
3. On first run, approve the Windows installer and complete Tailscale login / HTTPS consent in the browser opened by the launcher. Existing login state is reused later.
4. Install Tailscale on the iPhone and sign in with the same Tailscale account. Phone installation and login must be completed on the phone.
5. **Point the iPhone's Camera at the launcher's QR code and tap the link.** The page authenticates automatically. Safari remembers the connection; bookmark the page for later use. Manual token entry is only an advanced fallback.

Keep the launcher open while using the bridge. Closing it stops its server and sleep guard; it does not stop or sign out Tailscale. The token remains outside the repository at `%LOCALAPPDATA%\ChatGPTRemoteController\pairing-token.txt`. Windows lock state and ChatGPT login are not bypassed.

The QR contains the private credential in a URL fragment, which is never sent to the server and is removed from the address bar immediately. Verified credentials are remembered in Safari `localStorage`; Disconnect clears them. Private browsing or clearing website data may require another scan. Pending messages remain in `sessionStorage`.

For source development, Python 3.10+ or uv is still required. `./start.ps1` installs and runs only the server; Tailscale automation is part of the EXE launcher. Build the EXE with `./build-exe.ps1` on Windows with working GUI support. The build script installs `.[windows,desktop]` and creates a single executable in `dist` and the project root.

The server binds to `127.0.0.1`; Tailscale Serve supplies private-network HTTPS. **Do not enable Funnel or public port forwarding.** See [Tailscale Serve setup](https://tailscale.com/kb/1312/serve) if HTTPS needs enabling. Tailscale identity is separate from ChatGPT identity.

## Using it

- **Sessions:** page through local chats, including project chats, ordered by last modification through official `thread/list`. Load 100 at a time; search filters loaded titles. The app's local project metadata supplies project labels for older builds. Selection uses the documented `codex://threads/<id>` link. This does not combine ordinary ChatGPT web chats or other hosts.
- **New local session:** open a new composer, then explicitly select **On this computer / 내 컴퓨터에서** in the execution-location menu. The first prompt creates the conversation. Unknown controls cause an error without sending a message.
- **Send:** find the composer with UIA, paste Unicode and press Enter. Coordinate fallback is off by default. Clear existing drafts and enable Enter-to-send in ChatGPT.
- **Preview:** locate ChatGPT on any connected monitor, including a minimized or background window. Restore or activate it when capture/input requires it. Background GPU capture falls back to foreground capture of the target app. UIA response-text extraction is not implemented. ChatGPT must be running and Windows unlocked.
- **Displays:** dim supported WMI/DDC panels. Disable external video output when a connected external display cannot dim, keeping the internal laptop panel active. Restore output topology and saved brightness. Separate external-output buttons are also available. This controls video output rather than a monitor's physical power switch; standby/no-signal behavior is hardware-dependent. Demo mode never changes hardware.

The launcher hands the same credential to its child server and verifies an authenticated health response before showing a QR code. Native window status polling never waits for UIA traversal or capture, and UI work is serialized on one dedicated thread. Client status requests do not overlap; authentication, app availability, and transport failures have separate messages.

## Reconnection and uncertain sends

Temporary network errors retain the token and trigger reconnection. A pending message keeps its request ID across page reloads in `sessionStorage`. After a lost response, retrying the same text uses the same ID. **전송 결과 확인** queries the server without typing again. Only select **화면 확인 후 새로 작성** after checking the desktop preview; that permits a fresh send.

The server remembers up to 2,000 request IDs in memory and refuses new IDs at capacity. Restarting clears that memory; the mobile client detects the new server instance and refuses to replay an old pending message blindly. Credentials persist until disconnected or website data is cleared. Pending text stays only in the current browser session.

## Power behavior

The real bridge holds `ES_CONTINUOUS | ES_SYSTEM_REQUIRED` on one dedicated thread and releases it on shutdown. It inhibits idle system sleep and permits normal display timeout. It does not force the display to remain dark during input, change your power plan, bypass Windows locking, or override deliberate Sleep/lid-close actions. Foreground input needs an unlocked session. Use `--allow-sleep` or `start.ps1 -AllowSleep` to disable the guard.

## Diagnostics and calibration

Run `python -m remote_bridge.app --list-windows` or `--inspect` locally. Output can contain private titles: do not publish it. Default matching requires a ChatGPT-titled window owned by ChatGPT.exe; adjust `CHATGPT_WINDOW_RE` and `CHATGPT_PROCESS_RE` only when necessary.

If UIA cannot identify one editor, it stops. To opt into a manually calibrated fallback, set `REMOTE_ALLOW_COORDINATE_INPUT=1`, `REMOTE_COMPOSER_X=0.50`, and `REMOTE_COMPOSER_Y=0.88` in the shell that starts the bridge. `.env.example` documents variables but is not automatically loaded.

## Development and limits

Run `pip install -e '.[test]'`, `pytest -q`, and `node --test tests/test_mobile.cjs` (Node 22+). Node is only needed for frontend tests. `python -m remote_bridge.app --demo` runs without controlling ChatGPT.

An acknowledgment means input was delivered, not that ChatGPT accepted or answered it. UIA and execution-location controls depend on app version. Preview captures a foreground screen region and may be covered by other windows. This tool cannot change ChatGPT's storage, retention, or sync policies. Actual Windows checks and untested iPhone/external-network behavior are recorded in [TESTING.md](TESTING.md).

All API routes require the pairing token. No message bodies are intentionally logged. Keep credentials and screenshots out of public commits. Unofficial project, MIT licensed: [LICENSE](LICENSE).
