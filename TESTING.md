# Verification

## Mobile conversation UI — 2026-10-24 (v0.5.0)

- The official read-only `thread/turns/list` endpoint returned the existing dedicated test chat's user and assistant messages. The bridge returns these visible message types only, not reasoning or raw tool output. It does not resume a thread or call a model API to read history.
- An initial real send failed before input because UIA did not expose a usable composer at that instant. The cause was not isolated. Added navigation readiness waiting and per-monitor DPI awareness. With the actual ChatGPT window placed on the external monitor, sent one short test prompt and read both the new user message and actual `POCKET_CHAT_UI_OK` assistant reply through history (~6.14 seconds). Restored the original chat and window placement. This demonstrates the tested external-monitor path after the changes; it does not establish which change fixed the initial failure.
- Real WebKit browser, using Playwright's iPhone 13 device settings with an explicit 390×844 viewport: selected a chat, rendered text/Markdown/code/table, sent through the actual demo HTTP server, received a demo reply, and reloaded with authentication and chat selection intact. This is WebKit mobile emulation, not a physical iPhone, iOS Simulator, camera scan, or cellular test.
- In the same WebKit run, mocked history pages verified older-message loading, changing assistant content without losing the reader's scroll position, and rejection of script/image/JavaScript-link payloads. A shortened viewport kept the composer visible; this does not simulate every iOS keyboard behavior. The 1280×900 layout also had no page overflow.
- Kept the existing four Python and four Node regression checks. Extended the existing access-control check to cover the new message endpoint. Markdown dependencies are bundled locally (marked and DOMPurify), with licenses; no CDN assets are loaded by the phone.
- Built and launched the actual v0.5.0 standalone EXE. Authenticated real history, catalog, non-black preview, and packaged GUI checks passed. Replaced and restarted the Desktop executable. The running service returned the test conversation as text locally (~0.156 seconds) and through the actual Tailscale HTTPS address from this laptop. This remains distinct from a phone/cellular test.
- Stored history is polled every 2 seconds. The read-only server can expose a reconstructed/interrupted status for a turn owned by another process; token-by-token streaming and exact live-generation status are not established. Attachments are labeled rather than served as arbitrary local files. Interactive approvals remain in Notebook controls.

## Recent chats, multi-monitor operation, and reconnection — 2026-10-24

- Observed HTTP 401 from the previously running server using the persisted credential, while Tailscale itself reported Running/online. The exact origin of the old credential mismatch was not established. The launcher now explicitly hands its pairing credential to the child and requires authenticated readiness before showing a QR.
- The documented local `thread/list` API returned 159 non-archived interactive local chats over two pages, sorted by `updated_at` descending. App metadata identified 33 project members in the first 100. Titles, chat text, and project names are not included in these public results.
- A deep link to the pre-existing Pocket Bridge test chat was handled by the app and its exact-title ListItems were visible afterward. UIA did not expose a SelectionItem pattern, so that pattern could not independently confirm selection. No message was sent; the original chat was reopened.
- Native window status recognized ChatGPT behind another application immediately. A background GPU HWND capture was black on this machine; automatic foreground capture returned a non-black JPEG from the external display.
- Actually disabled external output: connected screens changed from 2 to 1, ChatGPT remained discoverable, and its preview worked. Restored the prior extended topology and verified 2 screens again; original window placement was restored.
- Internal brightness read as 70%; the external Generic Monitor returned no DDC brightness value. Unsupported external output is disconnected instead of pretending its brightness was changed.
- Retained four essential Python tests. Added one focused non-overlapping-health-poll check, for four Node checks total. The browser checks are simulated logic tests, not physical iPhone tests.
- The actual v0.4.0 EXE passed authenticated health, sorted/project-tagged catalog, non-black external-display preview, and packaged GUI checks. Replaced and restarted the running Desktop EXE. Its real service returned 100 chats (33 project members), 10 successful simultaneous status checks (maximum 0.078 seconds), and the same authenticated server instance over the actual Tailscale HTTPS URL from this laptop. This is not a physical iPhone/cellular check.

## QR connection and brightness — 2026-10-24

- Added one focused Node check: a QR fragment supplies authentication without manual entry, the credential is removed from the URL, and a new browser session reconnects using remembered local storage. Existing offline/retry checks still pass (3 Node checks total). This is browser-logic verification, not a physical iPhone camera/Safari test.
- Checked the real launcher layout with a sample credential: QR and exit controls are fully visible; manual credentials are hidden under advanced information.
- Confirmed Windows WMI brightness support on this laptop. Changed 70% to 69% and restored 70%, checking the reported hardware values. The test did not set 0% or power off the display. A 0% setting is minimum brightness, not a guarantee of a black panel.
- The brightness API requires the existing pairing credential. Demo mode does not change hardware brightness.
- QR scanning with a physical phone and actual external-network access remain unverified.

## Windows EXE launcher — 2026-10-24

- Built a single windowed `PocketChatGPTBridge.exe` with the Python interpreter, Windows input libraries, GUI, and web assets included. No external Python executable is used by the packaged app.
- Launched the packaged GUI and checked the connection-address, QR, and masked-token layout. The packaging check skips installation and networking.
- Started the actual frozen executable from an unrelated temporary working directory, both with the demo driver and with the real Windows driver. The web UI loaded, unauthenticated status returned 401, authenticated status returned 200, and the real driver reported an active sleep guard. No chat messages were sent in these checks.
- Verified that closing the launcher's Windows job terminates both the onefile bootloader and its server child, leaving no listener on port 8765.
- Downloaded the current official Tailscale MSI and successfully verified its Authenticode signature and Tailscale publisher. The installer was not executed during verification.
- Simulated only the first-run installation/login/Serve sequence, an empty `null` Serve configuration, and browser handoff for login URLs. These checks do not establish an actual Tailscale installation or connection.
- Physical iPhone, cellular access, real account login, and HTTPS consent still need a user-assisted end-to-end check. The launcher presents those first-run approvals; it does not bypass them.

Tested on Windows on 2026-10-23. Results below distinguish real desktop execution from simulated network/browser cases.

## Automated regression checks

- **4 essential Python checks:** unauthorized access cannot read or send; a lost reply does not duplicate the message; the server keeps the system awake and releases its request on shutdown; ambiguous editors stop before input.
- **2 essential Node checks:** offline startup preserves pairing and reconnects; manual retry retains the request ID after a lost reply.
- Removed duplicate authentication checks, HTML/asset presence checks, demo session-list checks, token-generation internals, and secondary edge cases. CI uses one Python version, 3.12, rather than a version matrix.
- These browser-logic tests execute the real `app.js` with simulated DOM and network objects. They are not an iPhone Safari test.
- PowerShell startup script syntax and an actual `start.ps1 -Demo -Port 18766` install/start were checked: the status endpoint returned HTTP 200 with the demo driver and no sleep request. The demo server was then stopped. Python source compilation passed.

## Actual Windows / ChatGPT checks

The real Python bridge, not the demo driver, was used against the installed ChatGPT desktop app. The test opened dedicated local Work test conversations and sent only a harmless instruction to reply with an exact marker without using tools or files.

| Check | Result |
| --- | --- |
| Locate ChatGPT by title and executable | Passed |
| Read actual UIA sidebar candidates through the API | Passed |
| Open a new composer and choose the local execution menu item | Passed |
| Paste a Korean prompt and send it to the desktop app through `/api/send` | Passed |
| Observe an actual assistant reply `POCKET_BRIDGE_V020_OK` | Passed |
| Retrieve a JPEG preview through `/api/preview` | Passed |
| Query the completed request without typing again | Passed |
| Replay the same ID and observe exactly one assistant reply | Passed |
| Switch to the first test conversation and back to the second | Passed on refreshed retry |
| Acquire the actual Windows sleep-inhibition request | Passed |
| Stop the test server after execution | Passed |

One session-switch attempt returned HTTP 409; a refreshed retry completed both directions. The cause of that first rejection was not established. The bridge intentionally rejects stale or ambiguous sidebar IDs, so refresh the list after sidebar changes rather than blindly retrying a stale selection.

Private sidebar titles, raw UIA dumps, pairing tokens, and full desktop screenshots are not included in this public repository.

## Still unverified

- A physical iPhone using Safari or a Home Screen app.
- An actual cellular/external-network connection through Tailscale Serve.
- Input and preview while the physical display is powered off.
- Long idle periods, automatic Windows locking, suspend/resume, lid changes, or reboot recovery.
- Other ChatGPT builds, languages, multiple app windows, or display scaling arrangements.

The real test confirms local API → actual desktop conversation → actual reply → preview. It does not establish every unattended or mobile scenario.

## Reproduce automated checks

```text
pip install -e '.[test]'
pytest -q
node --test tests/test_mobile.cjs
python -m compileall -q remote_bridge
```

Node 22+ is needed for the frontend regression tests. Use `--demo` to inspect the UI without touching ChatGPT. For a real test, open a separate local conversation, keep the Windows session unlocked, and check the visible result rather than treating `input_sent` as proof of model completion.
