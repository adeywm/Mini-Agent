"""Human approval decisions. Used by the API; the graph and broker re-verify independently.

Rules: the approver must be authenticated (caller's job) and authorized (allowlist), must
not be the requester (separation of duties), must quote the exact plan digest, and a
decision can be made once, before expiry.
"""

from __future__ import annotations

from typing import Literal

from infra_agent.models.domain import ApprovalRequest, AuditEvent, utcnow
from infra_agent.persistence.store import Store


class ApprovalError(PermissionError):
    pass


def decide(
    store: Store,
    approval_id: str,
    approver: str,
    decision: Literal["approve", "reject"],
    plan_digest: str,
    allowed_approvers: frozenset[str],
) -> ApprovalRequest:
    approval = store.get_approval(approval_id)
    if approval is None:
        raise ApprovalError("approval not found")
    if approver not in allowed_approvers:
        raise ApprovalError("approver not authorized")
    if approver == approval.requester:
        raise ApprovalError("requester cannot approve their own plan")
    if approval.plan_digest != plan_digest:
        raise ApprovalError("plan digest mismatch - the plan changed; review the new plan")
    if approval.decision != "pending":
        raise ApprovalError(f"approval already {approval.decision}")
    if approval.expires_at <= utcnow():
        updated = approval.model_copy(update={"decision": "expired"})
        store.save_approval(updated)
        raise ApprovalError("approval expired")
    updated = approval.model_copy(
        update={
            "decision": "approved" if decision == "approve" else "rejected",
            "approver": approver,
        }
    )
    store.save_approval(updated)
    store.append_audit(
        [
            AuditEvent(
                actor=approver,
                action=f"approval:{decision}",
                target=approval_id,
                outcome="allowed" if decision == "approve" else "denied",
                correlation_id=approval.task_id,
                payload_digest=plan_digest,
            )
        ]
    )
    return updated
