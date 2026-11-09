import { useCallback, useEffect, useState } from "react";
import { api, getToken, setToken, type Approval, type ApprovalView, type AuditEvent, type Task } from "./api";

// All server data is rendered as text (React escapes it). Plan parameters, logs and
// event context are untrusted content and must never be rendered as HTML.

const TERMINAL = new Set(["COMPLETED", "PARTIALLY_COMPLETED", "DENIED", "REJECTED", "EXPIRED", "FAILED", "CANCELLED"]);
const RECOVERABLE = new Set(["FAILED", "PARTIALLY_COMPLETED", "NEEDS_REVIEW"]);

function useAsync() {
  const [error, setError] = useState("");
  const run = useCallback(async (fn: () => Promise<unknown>) => {
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);
  return { error, run };
}

function TokenBar({ onChange }: { onChange: () => void }) {
  const [value, setValue] = useState(getToken());
  return (
    <form
      className="bar"
      onSubmit={(e) => {
        e.preventDefault();
        setToken(value.trim());
        onChange();
      }}
    >
      <label>
        API token{" "}
        <input type="password" value={value} onChange={(e) => setValue(e.target.value)} autoComplete="off" />
      </label>
      <button type="submit">Use token</button>
    </form>
  );
}

function SubmitTask({ onDone }: { onDone: () => void }) {
  const [intent, setIntent] = useState("");
  const [account, setAccount] = useState("dev");
  const [region, setRegion] = useState("ap-south-1");
  const [repos, setRepos] = useState("");
  const { error, run } = useAsync();
  return (
    <section>
      <h2>New task</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void run(async () => {
            await api.submit({
              intent,
              account_alias: account,
              region,
              repositories: repos.split(",").map((r) => r.trim()).filter(Boolean),
            });
            setIntent("");
            onDone();
          });
        }}
      >
        <textarea required minLength={3} value={intent} onChange={(e) => setIntent(e.target.value)}
          placeholder="e.g. Investigate why the api service is unhealthy and fix it" />
        <div className="row">
          <input value={account} onChange={(e) => setAccount(e.target.value)} aria-label="account alias" />
          <input value={region} onChange={(e) => setRegion(e.target.value)} aria-label="region" />
          <input value={repos} onChange={(e) => setRepos(e.target.value)} placeholder="owner/repo, ..." aria-label="repositories" />
          <button type="submit">Submit</button>
        </div>
      </form>
      {error && <p className="error">{error}</p>}
    </section>
  );
}

function ApprovalCard({ approval, onDone }: { approval: Approval; onDone: () => void }) {
  const [view, setView] = useState<ApprovalView | null>(null);
  const { error, run } = useAsync();
  useEffect(() => {
    void run(async () => setView(await api.approval(approval.id)));
  }, [approval.id, run]);
  const decide = (d: "approve" | "reject") =>
    run(async () => {
      await api.decide(approval.id, d, approval.plan_digest);
      onDone();
    });
  return (
    <article className="card">
      <header>
        <strong>{approval.id}</strong> for task <code>{approval.task_id}</code> - requested by {approval.requester},
        expires {new Date(approval.expires_at).toLocaleString()}
      </header>
      <p>
        Plan digest <code>{approval.plan_digest}</code>; needs approval for: {approval.scope.join(", ")}
      </p>
      {view?.plan && (
        <table>
          <thead>
            <tr><th>Step</th><th>Target</th><th>Operation</th><th>Parameters</th><th>Expected</th><th>Cost/mo</th></tr>
          </thead>
          <tbody>
            {view.plan.operations.map((op) => (
              <tr key={op.operation_id} className={approval.scope.includes(op.operation_id) ? "needs" : ""}>
                <td>{op.operation_id}</td>
                <td>{op.provider} {op.account_alias}/{op.region} ({op.environment})</td>
                <td><code>{op.service}:{op.operation}</code></td>
                <td><pre>{JSON.stringify(op.parameters, null, 2)}</pre></td>
                <td>{op.expected_outcome}</td>
                <td>{op.estimated_cost_change_usd_monthly ?? "unknown"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="row">
        <button className="approve" onClick={() => void decide("approve")}>Approve exact plan</button>
        <button className="reject" onClick={() => void decide("reject")}>Reject</button>
      </div>
      {error && <p className="error">{error}</p>}
    </article>
  );
}

function TaskRow({ task, onChanged }: { task: Task; onChanged: () => void }) {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const { error, run } = useAsync();
  return (
    <>
      <tr>
        <td><code>{task.id}</code></td>
        <td className={`status s-${task.status}`}>{task.status}</td>
        <td>{task.intent.split("\n")[0]}</td>
        <td>{task.account_alias}/{task.region}</td>
        <td>{task.requester}</td>
        <td>
          <button onClick={() => void run(async () => setEvents(events ? null : await api.events(task.id)))}>
            {events ? "Hide" : "Timeline"}
          </button>
          {!TERMINAL.has(task.status) && (
            <button onClick={() => void run(async () => { await api.cancel(task.id); onChanged(); })}>Cancel</button>
          )}
          {RECOVERABLE.has(task.status) && (
            <button onClick={() => void run(async () => { await api.recover(task.id); onChanged(); })}>Recover</button>
          )}
        </td>
      </tr>
      {(events || error) && (
        <tr>
          <td colSpan={6}>
            {error && <p className="error">{error}</p>}
            <ol className="timeline">
              {events?.map((e) => (
                <li key={e.id} className={`o-${e.outcome}`}>
                  {new Date(e.timestamp).toLocaleTimeString()} <strong>{e.action}</strong> [{e.outcome}] {e.actor}: {e.detail}
                </li>
              ))}
            </ol>
          </td>
        </tr>
      )}
    </>
  );
}

export default function App() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [frozen, setFrozen] = useState<boolean | null>(null);
  const { error, run } = useAsync();

  const refresh = useCallback(() => {
    void run(async () => {
      const [t, a] = await Promise.all([api.tasks(), api.approvals()]);
      setTasks(t);
      setApprovals(a);
    });
  }, [run]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 10_000);
    return () => clearInterval(timer);
  }, [refresh]);

  return (
    <main>
      <h1>infra-agent console</h1>
      <TokenBar onChange={refresh} />
      {error && <p className="error">{error}</p>}
      <section>
        <h2>Pending approvals ({approvals.length})</h2>
        {approvals.length === 0 && <p>Nothing waiting for approval.</p>}
        {approvals.map((a) => <ApprovalCard key={a.id} approval={a} onDone={refresh} />)}
      </section>
      <SubmitTask onDone={refresh} />
      <section>
        <h2>Tasks</h2>
        <table>
          <thead>
            <tr><th>Task</th><th>Status</th><th>Intent</th><th>Scope</th><th>Requester</th><th /></tr>
          </thead>
          <tbody>
            {tasks.map((t) => <TaskRow key={t.id} task={t} onChanged={refresh} />)}
          </tbody>
        </table>
      </section>
      <section className="danger">
        <h2>Kill switch (admin)</h2>
        <p>Freezing blocks every AWS and GitHub write immediately. It cannot undo writes already made.</p>
        <button onClick={() => void run(async () => setFrozen((await api.killSwitch(true)).writes_frozen))}>Freeze writes</button>
        <button onClick={() => void run(async () => setFrozen((await api.killSwitch(false)).writes_frozen))}>Unfreeze</button>
        {frozen !== null && <p>Writes frozen: <strong>{String(frozen)}</strong></p>}
      </section>
    </main>
  );
}
