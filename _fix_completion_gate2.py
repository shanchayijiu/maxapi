# -*- coding: utf-8 -*-
from pathlib import Path
import ast

path = Path(__file__).resolve().parent / "maxapi_server.py"
src = path.read_text(encoding="utf-8")

old = '''def _midflight_should_allow_end_turn(messages, answer_text="", reason_text=""):
    """Sol mid-flight: let first-pass prose end_turn through when work is done.

    Live evidence (Codex++ thrash): after settings fix succeeded, every turn still
    hit structural mid-flight -> auto->required -> often terminal-force, forcing
    Write-Output done phrases dozens of times (msgs 380->430+, 15-50s/turn).
    Gate fires when:
      A) first-pass answer claims done, OR
      B) recent tool outputs claim done AND latest user is only a short continue
         nudge (or empty) without a new strong action request.
    Does NOT fire on fresh strong action requests ("Call Bash echo X").
    """
    ans = (answer_text or "").strip()
    if ans and _looks_like_task_complete(ans):
        return True
    results = _recent_tool_result_texts(messages, limit=6)
    if not results:
        return False
    recent_done = any(_looks_like_task_complete(t) for t in results[:4])
    if not recent_done:
        return False
    user = (_latest_user_text(messages) or "").strip()
    # Fresh strong action after done-results still escalates (real new work).
    if user and _RE_TOOL_STRONG.search(user) and not _looks_like_task_complete(user):
        if len(user) >= 8 and not _RE_TOOL_HOWTO.search(user):
            return False
    # Short continue / ok / 做 after done-results -> allow stop (break thrash)
    if not user or len(user) < 24:
        return True
    if _looks_like_task_complete(user):
        return True
    if len(user) <= 200 and _RE_TASK_DONE.search(user):
        return True
    return False
'''

new = '''def _user_text_after_latest_tool(messages):
    """User text strictly after the newest tool payload; '' if none (tool-result tail)."""
    msgs = [m for m in (messages or []) if isinstance(m, dict)]
    last_tool_i = -1
    for i, m in enumerate(msgs):
        if _message_has_tool_payload(m):
            last_tool_i = i
    if last_tool_i < 0:
        return ""
    parts = []
    for m in msgs[last_tool_i + 1 :]:
        if m.get("role") == "user" and not _message_has_tool_payload(m):
            t = _message_text_blob(m.get("content"))
            if t and t.strip():
                parts.append(t.strip())
    return "\\n".join(parts)


def _midflight_should_allow_end_turn(messages, answer_text="", reason_text=""):
    """Sol mid-flight: let first-pass prose end_turn through when work is done.

    Live evidence (Codex++ thrash): after settings fix succeeded, every turn still
    hit structural mid-flight -> auto->required -> often terminal-force, forcing
    Write-Output done phrases dozens of times (msgs 380->430+, 15-50s/turn).
    Gate fires when:
      A) first-pass answer claims done, OR
      B) recent tool outputs claim done AND there is no new strong user action
         after those tools (tool-result tail, short nudge, or done-claim user).
    Does NOT fire on fresh strong action requests after the tool exchange.
    """
    ans = (answer_text or "").strip()
    if ans and _looks_like_task_complete(ans):
        return True
    results = _recent_tool_result_texts(messages, limit=6)
    if not results:
        return False
    recent_done = any(_looks_like_task_complete(t) for t in results[:4])
    if not recent_done:
        return False
    # Only the user text AFTER the latest tool exchange counts as a new request.
    # Original task text before tools must not keep escalate forever.
    user = (_user_text_after_latest_tool(messages) or "").strip()
    if not user:
        return True  # pure tool-result continuation after done output
    if _looks_like_task_complete(user):
        return True
    # Fresh strong action after done-results still escalates (real new work).
    if _RE_TOOL_STRONG.search(user) and not _looks_like_task_complete(user):
        if len(user) >= 8 and not _RE_TOOL_HOWTO.search(user):
            return False
    # Short continue / ok / 做 after done-results -> allow stop (break thrash)
    if len(user) < 24:
        return True
    if len(user) <= 200 and _RE_TASK_DONE.search(user):
        return True
    # Longer non-done, non-strong user after tools: still allow stop when results
    # already said done (model is looping status chatter, not new work).
    if not _RE_TOOL_ACTION.search(user):
        return True
    return False
'''

if old not in src:
    # already fixed?
    if "_user_text_after_latest_tool" in src:
        print("already has _user_text_after_latest_tool")
    else:
        idx = src.find("def _midflight_should_allow_end_turn")
        print(repr(src[idx:idx+600]))
        raise SystemExit("old midflight fn not found")
else:
    src = src.replace(old, new, 1)
    # normalize join newline
    src = src.replace('return "\\\\n".join(parts)', 'return "\\n".join(parts)')
    ast.parse(src)
    path.write_text(src, encoding="utf-8")
    print("rewrote midflight gate ok")
