## Root causes (ranked)

1. **Client routing is wrong.** The failing Claude Code traffic is not reaching maxapi. cc-switch currently selects provider `tsu`, routing to `https://api.831260.xyz`; the local `masapi` provider at `http://127.0.0.1:8080` is disabled (`is_current=0`).

2. **The failing request contains no tools.** The decisive request is `rid=70402004`: `[chat] ... tools=0 ...`. Maxapi cannot execute tools that the client never sends. The model's “no execution tools” response is correct.

3. **The Codex path is also misconfigured.** Codex points at `localhost:3010`, which is not listening, while another config points at `57321/v1` using the Responses API and a different model. That session is unrelated to the working maxapi tool path.

4. **The pasted “做” session therefore never had maxapi tools.** Deleting invalid `Write(**)` settings rules changes policy text, not tool injection. It is expected to feel like no fix.

## maxapi vs client

Maxapi's shipped behavior is working: when the client sends tools, logs show `tools=1`, structural mid-flight escalation runs, and 28/28 passes. The 86 messages-path requests with tools confirm this.

Maxapi should still add an operational guard: for agent-capable models, log or reject `tools=0` with a clear diagnostic such as “agent request arrived without tools; inspect client routing.” It may also expose a non-empty `/v1/models` response if 15721 is intended as a discoverable provider. Neither change can manufacture tools in a malformed client request.

## Fix order

1. Make `masapi` the active Claude Code provider in cc-switch, with base URL `http://127.0.0.1:8080` and the `gpt-5.6-sol`/Sonnet alias.
2. Restart Claude Code and verify the actual request reaches maxapi.
3. Run a minimal tool-capable task and require logs showing `messages`, `tools>0`, and the expected model.
4. Do not use `15721` or `api.831260.xyz` until their provider routing is intentionally configured.
5. For Codex, either configure it against a live, compatible maxapi endpoint and API wire format, or keep it separate. Do not mix its `3010`/`57321` Responses configuration with Claude Code’s Chat/Messages path.
6. Ignore `settings.local.json` as a tool-enablement mechanism.

## Verdict

**APPROVE diagnosis.** This is primarily a client/provider-routing failure, not a GPT-series or d1eb247 failure. Next actions: activate `masapi`, restart the client, confirm `tools>0` in maxapi logs, then test an actual file/terminal operation.