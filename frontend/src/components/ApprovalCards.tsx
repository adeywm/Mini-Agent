import { AlertTriangle, Check, ShieldAlert, X } from "lucide-react";
import { useState } from "react";
import type { Approval } from "../api";
import { humanTool, splitTool } from "../lib";
import { ProviderIcon } from "./Icons";

interface Props {
  approvals: Approval[];
  busy: boolean;
  onDecide: (approval: Approval, decision: "approve" | "reject", message?: string) => void;
}

/** One card per change (L2/L3 tool call) waiting for the user. Approve runs exactly the
 *  call shown; reject tells the agent not to run it. */
export function ApprovalCards({ approvals, busy, onDecide }: Props) {
  if (!approvals.length) return null;
  return (
    <div className="approvals">
      {approvals.map((a) => (
        <ApprovalCard key={a.id} approval={a} busy={busy} onDecide={onDecide} />
      ))}
    </div>
  );
}

function ApprovalCard({ approval: a, busy, onDecide }: { approval: Approval } & Omit<Props, "approvals">) {
  const [reason, setReason] = useState("");
  const { tool } = splitTool(a.name);
  const command = typeof a.args.cli_command === "string" ? a.args.cli_command : null;
  return (
    <section className={`approval-card l${a.level}`} aria-label={`Approval needed: ${humanTool(tool)}`}>
      <header>
        <ShieldAlert size={16} aria-hidden />
        <strong>{a.level === 3 ? "Delete / IAM change" : "Create / update"} needs your approval</strong>
        <span className={`lvl l${a.level}`}>L{a.level}</span>
      </header>
      <dl className="approval-facts">
        <dt>Connection</dt>
        <dd>
          <ProviderIcon kind={a.kind} size={12} /> {a.connection} <span className="muted">· {a.account}</span>
        </dd>
        <dt>Action</dt>
        <dd>
          {humanTool(tool)} <code>{tool}</code>
        </dd>
        <dt>Why L{a.level}</dt>
        <dd>{a.reason}</dd>
      </dl>
      {command ? (
        <pre className="approval-args"><code>{command}</code></pre>
      ) : (
        <pre className="approval-args"><code>{JSON.stringify(a.args, null, 2)}</code></pre>
      )}
      {a.warnings.map((w) => (
        <p key={w} className="approval-warning">
          <AlertTriangle size={14} aria-hidden /> {w}
        </p>
      ))}
      {!a.approvable && (
        <p className="approval-warning" role="alert">
          <AlertTriangle size={14} aria-hidden /> Some values are hidden because they look like secrets, so this change
          can only be rejected.
        </p>
      )}
      <p className="muted small">
        Approving runs exactly this call with your connection's own permissions. Current values are in the
        assistant's message above.
      </p>
      <div className="approval-actions">
        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Reason for rejecting (optional)"
          aria-label="Reason for rejecting"
          disabled={busy}
        />
        <button className="danger-outline" disabled={busy} onClick={() => onDecide(a, "reject", reason.trim() || undefined)}>
          <X size={15} /> Reject
        </button>
        <button className="primary" disabled={busy || !a.approvable} onClick={() => onDecide(a, "approve")}>
          <Check size={15} /> Approve
        </button>
      </div>
    </section>
  );
}
