# Post-mortem: verification silently ran against a stale server

**Status:** resolved · **Severity:** SEV-3 (no user impact; corrupted trust in the dev/verify loop) · **Component:** mock agent server (`frontend/mock/agent-server.mjs`)

## Summary

While verifying the human-in-the-loop plan/approval feature, an automated test
reported behavior from an **older build of the server** than the one I had just
written. The new server had crashed on startup with `EADDRINUSE` because a
**stale instance from a previous task was still bound to port 8787**, and the
crash was easy to miss in backgrounded output. For several minutes I was
reading test results from code that was not the code under test.

No production or user impact — this was a local development incident. The real
damage was to **trust in the verification loop**: a green-looking run that
proves nothing because you can't tell what it actually exercised.

## Impact

- One verification cycle produced misleading results (old plan script,
  `404`s on the new `/plan` and `/decision` routes).
- ~5 minutes lost to confusion before the root cause was found.
- **Near-miss:** had the old and new servers been only *slightly* different, the
  test could have **passed against stale code** — a false PASS that ships a bug.

## Timeline (local time, condensed)

1. **Earlier task (WebGL explorer):** started the mock server in the background
   for smoke tests; tore it down with `pkill` at the end. One detached instance
   survived the cleanup.
2. **This task (HITL):** started a "new" mock with the plan-and-execute code.
   It threw `EADDRINUSE` and exited — but as a backgrounded process its stack
   trace landed in a log file I wasn't watching.
3. The stale instance kept answering on `:8787`.
4. The curl test hit `:8787` and got the **old** server: it streamed the old
   profile→reduce→inspect script and returned `404` for `/plan` and `/decision`.
5. Noticed the anomaly (an `inspect` step that no longer exists; `404`s on routes
   I'd just added), inspected `lsof -i:8787` and the mock log, found the
   `EADDRINUSE` crash.
6. Killed all instances, re-ran clean → correct results.

## Root cause

**No single-instance guarantee and no identity signal.** The server bound a
fixed port with no detection of an already-running instance, exited on
`EADDRINUSE` with an unhandled raw stack trace, and exposed **no way for a
caller to ask "which build are you?"**. Combined with backgrounded process
output, a crashed-new / surviving-old state was invisible at the call site.

### 5 whys

1. *Why were results wrong?* The test hit an old server build.
2. *Why was an old build running?* A stale instance from a prior task never died.
3. *Why didn't the new server take over?* It crashed on `EADDRINUSE` instead of
   replacing or refusing loudly.
4. *Why wasn't the crash noticed?* It was a backgrounded process; the error went
   to a log, not the foreground.
5. *Why didn't the test catch the mismatch?* The test asserted **behavior** but
   never asserted **which build / version** it was talking to.

## What went well

- The new feature added routes (`/plan`, `/decision`) the old build lacked, so
  the mismatch surfaced as obvious `404`s. Without that, it would have been
  far harder to spot — which is exactly the near-miss.

## Remediations

| # | Action | Status |
|---|--------|--------|
| 1 | `GET /health` returns `{ version, uptime_s, activeRuns }` so any caller can assert the build | ✅ done |
| 2 | Handle `EADDRINUSE` explicitly: log an actionable message + exit non-zero, not a raw trace | ✅ done |
| 3 | Graceful shutdown on `SIGINT`/`SIGTERM` so instances don't linger | ✅ done |
| 4 | Structured `server.start` log line carries the version | ✅ done |
| 5 | Verification kills stragglers (`pkill -f agent-server.mjs`) before launching | ✅ done (process discipline) |
| 6 | Verify scripts assert `/health` `version` (build identity) before driving the app — abort loudly if the backend is unreachable, unidentified, or `EXPECT_VERSION`-mismatched | ✅ done |

## Lessons

- **A test is only as trustworthy as its ability to prove what it tested.**
  Behavioral assertions against an unidentified target can pass against the
  wrong thing. Assert identity (version/health), not just behavior.
- **Backgrounded processes are phantom state.** Detached dev servers outlive the
  task that spawned them; treat port ownership as something to verify, not
  assume.
- **Observability isn't only for production — it's how you trust your own loop.**
  The fix here (a `/health` version endpoint) is the same instinct as this
  project's trace inspector: a capable system you can't inspect is a system you
  can't trust, whether the "user" is a customer or yourself at the terminal.
