"""Assert the tool prompt now contains the EXECUTE-DON'T-NARRATE directive
(rule 13) and the strengthened reminder (no narration substitute)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import maxapi_server as M

tools = [{"type": "function", "function": {
    "name": "get_weather", "description": "x",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}]

p = M._make_tools_prompt(tools, "auto")
assert p is not None, "prompt should be built for non-empty tools"
# the EXECUTE-DO-NOT-NARRATE rule (rule 13) is present
assert "EXECUTE, DO NOT NARRATE" in p, "missing rule 13 directive"
assert "narration is not a substitute" in p.lower() or "narration is NEVER a substitute" in p, "missing narration-substitute clause"
# the anti-leak hardening line is still present
assert "Do NOT use any other tag format" in p, "anti-leak hardening line dropped"
# the strengthen reminder path adds the execute clause: build messages and check the tail reminder
msgs, enabled = M._build_messages_with_tools(tools, "auto", [{"role": "user", "content": "hi"}])
last = msgs[-1]
assert isinstance(last, dict) and last.get("role") == "system", "tail reminder must be system"
c = last.get("content", "")
assert "EXECUTE the action with a tool call in THIS turn" in c, "strengthened reminder missing execute clause"
assert "narration is not a substitute" in c.lower(), "strengthened reminder missing narration clause"
print("PASS: rule 13 + strengthened reminder in place")
sys.exit(0)
