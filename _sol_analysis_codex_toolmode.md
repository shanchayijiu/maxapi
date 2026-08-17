**A) Root cause**

`tool_mode=code_mode_only` is the leading root cause, but the evidence does not isolate it from `use_responses_lite=true`.

The decisive fact is that Codex sent `tools=0` for the failing GPT turn. Therefore, the model and maxapi never had tools available to call. Since maxapi accepts all 28 tools when supplied, this is a Codex request-construction/catalog issue, not a GPT tool-use capability issue.

**B) Catalog fix**

Yes. Removing `tool_mode=code_mode_only` and `multi_agent_version=v2`, while setting `use_responses_lite=false`, is the correct compatibility fix for routing Codex++ through maxapi’s `/chat` path with normal tool definitions.

Keep:

- `shell_type=shell_command`
- `apply_patch` as a freeform tool
- `supports_tool_use=true`

However, because three fields changed simultaneously, do not claim that `tool_mode` alone was conclusively proven.

**C) maxapi changes**

No functional maxapi change is indicated. The evidence shows:

- The failing request arrived with `tools=0`.
- `/v1/models` advertises tool support.
- maxapi accepts 28/28 tools when Codex sends them.

Recommended verification:

1. Reload/restart Codex++ so the edited catalog is not cached.
2. Start a fresh thread.
3. Confirm maxapi logs show `tools=28` or another nonzero count.
4. Confirm GPT emits an actual `exec_command` or `apply_patch` call.
5. Verify streaming preserves tool-call IDs, arguments, and tool-result messages.

**D) Decision**

**APPROVE diagnosis**, phrased as:

> Codex++ catalog metadata caused GPT requests on the chat path to omit tools. The catalog compatibility fix is appropriate. `tool_mode=code_mode_only` is the primary suspect, with `use_responses_lite=true` a possible contributing setting; maxapi is not the demonstrated fault.