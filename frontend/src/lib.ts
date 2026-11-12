import type { AssistantMessage, Chat, ChatNotice, Connection, Message, StreamEvent, ToolCall, ToolMessage } from "./api";

/** Combine errors for the same login while keeping distinct profiles separate. */
export function addAuthNotice(notices: ChatNotice[], event: Extract<StreamEvent, { type: "auth_required" }>): ChatNotice[] {
  const index = notices.findIndex((notice) => notice.type === "auth_required" &&
    notice.method === event.method && notice.profile === event.profile);
  if (index < 0) return [...notices, {
    type: "auth_required", sources: [event.source], profile: event.profile,
    method: event.method, errorTypes: [event.error_type],
  }];
  return notices.map((notice, i) => i === index && notice.type === "auth_required" ? {
    ...notice, sources: Array.from(new Set([...notice.sources, event.source])),
    errorTypes: Array.from(new Set([...notice.errorTypes, event.error_type])),
  } : notice);
}

export const LEVELS = {
  1: { short: "L1", label: "Read only" },
  2: { short: "L2", label: "Create / update" },
  3: { short: "L3", label: "Delete" },
} as const;

export interface ToolRef {
  connection: string; // connection slug (tool-name prefix)
  tool: string; // tool name without prefix
}

export function splitTool(name: string): ToolRef {
  const i = name.indexOf("__");
  if (i < 0) return { connection: "", tool: name };
  return { connection: name.slice(0, i), tool: name.slice(i + 2).replace(/^aws___/, "") };
}

export function kindOf(slug: string, connections: Connection[]): "aws" | "github" | undefined {
  return connections.find((c) => c.name.replace(/-/g, "_") === slug)?.kind;
}

export function humanTool(tool: string): string {
  return tool.replace(/_/g, " ");
}

/** One tool call with its result (if it has finished) and timing (if seen live). */
export interface ToolStep {
  call: ToolCall;
  result?: ToolMessage;
  ms?: number;
  /** A change (L2/L3) paused until the user approves or rejects it. */
  awaiting?: boolean;
}

/** A rendered conversation item: a user message, or one assistant turn made of segments. */
export type Segment = { kind: "text"; id: string; text: string; streaming?: boolean } | {
  kind: "reasoning";
  id: string;
  text: string;
} | {
  kind: "tools";
  id: string;
  steps: ToolStep[];
};
export type Item = { kind: "user"; text: string } | { kind: "assistant"; segments: Segment[] };

export function buildItems(messages: Message[], timings: Record<string, number>): Item[] {
  const results = new Map<string, ToolMessage>();
  for (const m of messages) if (m.role === "tool") results.set(m.tool_call_id, m);
  const items: Item[] = [];
  let turn: Extract<Item, { kind: "assistant" }> | null = null;
  for (const m of messages) {
    if (m.role === "user") {
      items.push({ kind: "user", text: m.content });
      turn = null;
      continue;
    }
    if (m.role !== "assistant") continue;
    if (!turn) {
      turn = { kind: "assistant", segments: [] };
      items.push(turn);
    }
    const a = m as AssistantMessage;
    if (a.reasoning) turn.segments.push({ kind: "reasoning", id: `r-${a.id}`, text: a.reasoning });
    if (a.content) turn.segments.push({ kind: "text", id: a.id, text: a.content, streaming: a.streaming });
    if (a.tool_calls.length) {
      const steps = a.tool_calls.map((call) => ({
        call,
        result: results.get(call.id),
        ms: timings[call.id],
      }));
      const prev = turn.segments[turn.segments.length - 1];
      if (prev?.kind === "tools") prev.steps.push(...steps);
      else turn.segments.push({ kind: "tools", id: `t-${a.id}`, steps });
    }
  }
  return items;
}

export function allSteps(messages: Message[], timings: Record<string, number>): ToolStep[] {
  return buildItems(messages, timings).flatMap((i) =>
    i.kind === "assistant" ? i.segments.flatMap((s) => (s.kind === "tools" ? s.steps : [])) : [],
  );
}

/** A closed response is not evidence that an unfinished tool call was cancelled. */
export function markPendingUnknown(
  messages: Message[], stopped: boolean, awaitingApproval: ReadonlySet<string> = new Set(),
): Message[] {
  const completed = new Set(messages.flatMap((m) => m.role === "tool" ? [m.tool_call_id] : []));
  // A change waiting for approval has not run; it is neither running nor unknown.
  const pending = messages.flatMap((m) => m.role === "assistant" ? m.tool_calls : [])
    .filter((call) => !completed.has(call.id) && !awaitingApproval.has(call.id));
  if (!pending.length) return messages;
  const content = stopped
    ? "Stopped by the user; the outcome of this step is unknown."
    : "Response ended before the tool result arrived; the outcome of this step is unknown.";
  return [...messages, ...pending.map((call): ToolMessage => ({
    role: "tool", tool_call_id: call.id, name: call.name, status: "error", content,
  }))];
}

export type StepState = "running" | "awaiting" | "ok" | "refused" | "rejected" | "error" | "stopped";

export function stepState(step: ToolStep): StepState {
  if (!step.result) return step.awaiting ? "awaiting" : "running";
  // Stopped steps have an unknown outcome: never present them as "not done".
  if (step.result.content.startsWith("Stopped by the user") ||
      step.result.content.startsWith("Response ended before the tool result arrived")) return "stopped";
  if (step.result.status !== "error") return "ok";
  // Rejected or dismissed approvals certainly did not run.
  if (/^(User rejected|Not run:)/.test(step.result.content)) return "rejected";
  return /^(Blocked:|Not allowed:|Unknown tool )/.test(step.result.content)
    ? "refused"
    : "error";
}

export function stepLabel(step: ToolStep): string {
  const state = stepState(step);
  return {
    running: "Running", awaiting: "Awaiting approval", ok: "Returned", refused: "Blocked",
    rejected: "Not run", error: "Error", stopped: "Outcome unknown",
  }[state];
}

export function formatMs(ms?: number): string {
  if (ms === undefined) return "";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function groupChats(chats: Chat[], now = new Date()): [string, Chat[]][] {
  const day = 86_400_000;
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const groups: Record<string, Chat[]> = {};
  const order = ["Today", "Yesterday", "Previous 7 days", "Older"];
  for (const c of chats) {
    const t = new Date(c.updated_at).getTime();
    const g = t >= start ? order[0] : t >= start - day ? order[1] : t >= start - 7 * day ? order[2] : order[3];
    (groups[g] ??= []).push(c);
  }
  return order.filter((g) => groups[g]).map((g) => [g, groups[g]]);
}

export function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}
