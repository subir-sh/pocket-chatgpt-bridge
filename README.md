# Pocket ChatGPT Bridge

Use your iPhone to control the Windows ChatGPT app left running at home. Your phone's official ChatGPT account stays separate. Prompts enter the desktop conversation and responses appear in a window preview. No OpenAI API or internal conversation database is used.

**[한국어 설명서](README.ko.md)** · [Actual verification and limits](TESTING.md)

## Setup

Windows 10/11, Python 3.10+ or uv, ChatGPT Desktop, and Tailscale on both devices are required.

1. Connect the laptop to power, leave the lid open and Windows unlocked, and open the desired local Work conversation.
2. Download or clone this repository and run `./start.ps1` in PowerShell. Alternatively, install with `pip install -e '.[windows]'` and run `pocket-bridge`.
3. Save the terminal's pairing token privately. It lives outside the repository at `%LOCALAPPDATA%\ChatGPTRemoteController\pairing-token.txt`.
4. Connect the iPhone and laptop to the same Tailscale network. Run `tailscale serve --bg 8765`, then `tailscale serve status` on the laptop.
5. Open the returned HTTPS `*.ts.net` URL in Safari, enter the token, and optionally use Share → Add to Home Screen.

The server binds to `127.0.0.1`; Tailscale Serve supplies private-network HTTPS. **Do not enable Funnel or public port forwarding.** See [Tailscale Serve setup](https://tailscale.com/kb/1312/serve) if HTTPS needs enabling. Tailscale identity is separate from ChatGPT identity.

## Using it

- **Sessions:** read visible UIA sidebar candidates, filter, scroll, and select. This is not a complete conversation database or global search.
- **New local session:** open a new composer, then explicitly select **On this computer / 내 컴퓨터에서** in the execution-location menu. The first prompt creates the conversation. Unknown controls cause an error without sending a message.
- **Send:** find the composer with UIA, paste Unicode and press Enter. Coordinate fallback is off by default. Clear existing drafts and enable Enter-to-send in ChatGPT.
- **Preview:** read the foreground window as JPEG, zoom, scroll, or click a location. UIA response-text extraction is not implemented.

## Reconnection and uncertain sends

Temporary network errors retain the token and trigger reconnection. A pending message keeps its request ID across page reloads in `sessionStorage`. After a lost response, retrying the same text uses the same ID. **전송 결과 확인** queries the server without typing again. Only select **화면 확인 후 새로 작성** after checking the desktop preview; that permits a fresh send.

The server remembers up to 2,000 request IDs in memory and refuses new IDs at capacity. Restarting clears that memory; the mobile client detects the new server instance and refuses to replay an old pending message blindly. Neither tokens nor pending text are permanent browser records: keep your pairing token available if the browser session ends.

## Power behavior

The real bridge holds `ES_CONTINUOUS | ES_SYSTEM_REQUIRED` on one dedicated thread and releases it on shutdown. It inhibits idle system sleep and permits normal display timeout. It does not force the display to remain dark during input, change your power plan, bypass Windows locking, or override deliberate Sleep/lid-close actions. Foreground input needs an unlocked session. Use `--allow-sleep` or `start.ps1 -AllowSleep` to disable the guard.

## Diagnostics and calibration

Run `python -m remote_bridge.app --list-windows` or `--inspect` locally. Output can contain private titles: do not publish it. Default matching requires a ChatGPT-titled window owned by ChatGPT.exe; adjust `CHATGPT_WINDOW_RE` and `CHATGPT_PROCESS_RE` only when necessary.

If UIA cannot identify one editor, it stops. To opt into a manually calibrated fallback, set `REMOTE_ALLOW_COORDINATE_INPUT=1`, `REMOTE_COMPOSER_X=0.50`, and `REMOTE_COMPOSER_Y=0.88` in the shell that starts the bridge. `.env.example` documents variables but is not automatically loaded.

## Development and limits

Run `pip install -e '.[test]'`, `pytest -q`, and `node --test tests/test_mobile.cjs` (Node 22+). Node is only needed for frontend tests. `python -m remote_bridge.app --demo` runs without controlling ChatGPT.

An acknowledgment means input was delivered, not that ChatGPT accepted or answered it. UIA and execution-location controls depend on app version. Preview captures a foreground screen region and may be covered by other windows. This tool cannot change ChatGPT's storage, retention, or sync policies. Actual Windows checks and untested iPhone/external-network behavior are recorded in [TESTING.md](TESTING.md).

All API routes require the pairing token. No message bodies are intentionally logged. Keep credentials and screenshots out of public commits. Unofficial project, MIT licensed: [LICENSE](LICENSE).
