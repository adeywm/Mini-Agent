# Approval Dashboard UX and Authorization

## Minimum screens

1. **Task inbox:** filters by status, account, environment, trigger, priority and time.
2. **Task detail:** user request, source event, investigation evidence, proposed plan and risk summary.
3. **Approval detail:** exact AWS/GitHub operations, account ID alias, region, resource IDs, estimated spend/uncertainty, security impact, plan digest, affected repositories and postconditions.
4. **Execution timeline:** approved by whom/when, API actions and request IDs, verification, errors and potential rollback.
5. **Policy/admin:** read-only inspection of active policies, role mappings and kill-switch status; policy changes use a separate privileged workflow.

## Human approval rules

- Authenticated approver through SSO/OIDC; enforce server-side role, team, account and separation-of-duties checks.
- The operator approves the **whole canonical plan**, not generic access to AWS. Present exact differences and scope and bind to plan digest.
- Approval expires; cancel invalidates. Replanned operations invalidate existing approval.
- Confirmation response is persisted transactionally and reflected in audit before graph resume.
- Reject or expire without executing. Fail closed when approval service is unavailable.
- For production, consider dual approvals for high-risk classes; approval never grants rights beyond IAM.

## Example API payloads

```json
{
  "approval_id": "<server-generated>",
  "plan_id": "<server-generated>",
  "plan_digest": "sha256:...",
  "decision": "APPROVE",
  "comment": "Reviewed the ECS service scope and rollback plan"
}
```

The client does not supply its own effective role or approver identity. These come from the authenticated session and server-side records.

## Accessibility and UI concerns

Readable structured diffs, keyboard approval controls, accessible risk labels, copyable resource IDs, clear cancellation, automatic refresh through SSE or polling, and explicit indication when cost estimates or rollback possibilities are uncertain. Never present an unverified cost figure as guaranteed.

## UI security tests

- Users cannot approve tasks outside assigned account/scope.
- CSRF protection or equivalent depending on auth mechanism.
- Expired/replayed approval rejected.
- Simulated approval cannot trigger broker execution.
- Changed operation plan requires new approval.
- WebSocket/SSE subscribers cannot see other users' secrets.
