// Typed client for the infra-agent API. The bearer token lives in sessionStorage only.

export type TaskStatus =
  | "RECEIVED" | "TRIAGING" | "INVESTIGATING" | "PLANNING" | "POLICY_REVIEW"
  | "AWAITING_APPROVAL" | "APPROVED" | "EXECUTING" | "VERIFYING" | "COMPLETED"
  | "PARTIALLY_COMPLETED" | "DENIED" | "REJECTED" | "EXPIRED" | "FAILED"
  | "CANCELLED" | "NEEDS_REVIEW";

export interface Task {
  id: string;
  requester: string;
  origin: string;
  intent: string;
  account_alias: string;
  region: string;
  repositories: string[];
  status: TaskStatus;
  created_at: string;
  last_updated: string;
}

export interface AuditEvent {
  id: string;
  actor: string;
  action: string;
  target: string;
  outcome: "allowed" | "denied" | "error" | "info";
  detail: string;
  timestamp: string;
}

export interface PlannedOperation {
  operation_id: string;
  provider: "aws" | "github";
  account_alias: string;
  environment: string;
  service: string;
  operation: string;
  region: string;
  resource_scope: string[];
  parameters: Record<string, unknown>;
  depends_on: string[];
  expected_outcome: string;
  estimated_cost_change_usd_monthly: number | null;
}

export interface Approval {
  id: string;
  task_id: string;
  plan_digest: string;
  scope: string[];
  requester: string;
  decision: string;
  expires_at: string;
}

export interface ApprovalView {
  approval: Approval;
  plan: { id: string; digest: string; operations: PlannedOperation[] } | null;
}

export interface User { username: string; roles: string[]; csrf_token: string }
let csrfToken = "";
export function setCsrf(value: string): void { csrfToken = value; }

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const resp = await fetch(path, {
    method,
    headers: {
      "X-Requested-With": "MiniAgent",
      ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    if (resp.status === 401 && path.startsWith("/v1/")) window.dispatchEvent(new Event("session-expired"));
    let detail = resp.statusText;
    try {
      detail = (await resp.json()).detail ?? detail;
    } catch {
      /* non-JSON error */
    }
    throw new Error(`${resp.status}: ${detail}`);
  }
  return (await resp.json()) as T;
}

export const api = {
  setupStatus: () => call<{ setup_required: boolean }>("GET", "/auth/setup"),
  setup: (username: string, password: string) => call<{ status: string }>("POST", "/auth/setup", { username, password }),
  login: (username: string, password: string) => call<User>("POST", "/auth/login", { username, password }),
  me: () => call<User>("GET", "/auth/me"),
  logout: () => call<{ status: string }>("POST", "/auth/logout"),
  tasks: (status?: string) => call<Task[]>("GET", `/v1/tasks${status ? `?status=${status}` : ""}`),
  events: (id: string) => call<AuditEvent[]>("GET", `/v1/tasks/${encodeURIComponent(id)}/events`),
  submit: (body: { intent: string; account_alias: string; region: string; repositories: string[] }) =>
    call<{ task_id: string }>("POST", "/v1/tasks", body),
  cancel: (id: string) => call<{ status: string }>("POST", `/v1/tasks/${encodeURIComponent(id)}/cancel`),
  recover: (id: string) => call<{ task_id: string }>("POST", `/v1/tasks/${encodeURIComponent(id)}/recover`),
  approvals: () => call<Approval[]>("GET", "/v1/approvals"),
  approval: (id: string) => call<ApprovalView>("GET", `/v1/approvals/${encodeURIComponent(id)}`),
  decide: (id: string, decision: "approve" | "reject", plan_digest: string) =>
    call<{ decision: string }>("POST", `/v1/approvals/${encodeURIComponent(id)}/decision`, {
      decision,
      plan_digest,
    }),
  killSwitch: (frozen: boolean) =>
    call<{ writes_frozen: boolean }>("POST", "/v1/admin/kill-switch", { frozen }),
};
