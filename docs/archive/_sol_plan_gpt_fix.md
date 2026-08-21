## Ranked root causes
1. `_auto_action_candidate` / `_should_escalate` does not recognize the structural mid-flight escalation signal, so escalation depends on incomplete lexical matching.
2. `tools=0` is likely introduced by the client request or client-side serialization; verify that the maxapi path receives and forwards tools without dropping them before changing escalation logic.
3. Shared escalation behavior could change Claude behavior or break its lock state; any protective condition should be scoped to `sol` when possible.
4. XFF, identity handling, and `busy != quota` behavior are unrelated and should not be treated as root causes.

## Minimal fix steps (ordered)
1. Trace the maxapi request through parsing, normalization, and forwarding to confirm whether tools are present at every boundary; ensure maxapi never drops tools.
2. Add the smallest structural mid-flight escalation check in `_auto_action_candidate` / `_should_escalate`, using parsed response/action structure rather than synonym or keyword lists.
3. Scope the new behavior to `sol` if shared handling could affect Claude.
4. Preserve existing XFF, identity, and `busy != quota` logic unchanged.
5. Keep the diff limited to `_auto_action_candidate`, `_should_escalate`, and only tiny helpers required by the structural check.
6. Add focused regression coverage for structural mid-flight escalation, client-originated `tools=0`, maxapi tool preservation, and Claude lock preservation.

## Acceptance tests
- sol: A structurally identifiable mid-flight escalation triggers the intended escalation; lexical synonyms alone do not trigger it; client-originated `tools=0` is not misclassified; maxapi preserves nonzero tools end to end.
- claude lock: Claude behavior and lock state remain unchanged, including requests with `tools=0`, `busy != quota`, XFF/identity variations, and non-escalation responses.

## Do NOT
- Add synonym or keyword lists.
- Infer a server-side escalation from `tools=0` before verifying the client request.
- Modify XFF handling, identity handling, or `busy != quota` semantics.
- Apply a broad shared fix when a `sol`-scoped condition protects Claude.
- Change code outside `_auto_action_candidate`, `_should_escalate`, and tiny supporting helpers.
- Require passwords, verification codes, payment credentials, or other sensitive information.

## APPROVE_PLAN or REVISE
APPROVE_PLAN