## APPROVE_SHIP

The final revision satisfies the stated ship gates:

- `_SOL_MODEL_IDS` uses the exact allowlist.
- Pending mid-flight handling skips the trailing user nudge and requires the immediately preceding tool payload.
- How-to and forbidden requests are blocked.
- Claude handling is lexical-only.
- XFF, identity, and `busy != quota` behavior remain untouched.
- `_accept_tool_suite.py` passes completely: **28/28**.
- Live checks pass:
  - Mid-flight “做 / 继续 / ok” reaches `tool_use`.
  - Settings “做” produces an `Edit` removing `Write(**)`.
  - Claude how-to produces zero tools.
  - `PONG` remains correct.

No remaining blocker is indicated for this revision.