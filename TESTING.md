# Verification

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
