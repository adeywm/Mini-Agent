import type { AssistantMessage, Chat, Connection, Message, ToolCall, ToolMessage } from "./api";

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
}

/** A rendered conversation item: a user message, or one assistant turn made of segments. */
export type Segment = { kind: "text"; id: string; text: string; streaming?: boolean } | {
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

export type StepState = "running" | "ok" | "refused" | "stopped";

export function stepState(step: ToolStep): StepState {
  if (!step.result) return "running";
  // Stopped steps have an unknown outcome: never present them as "not done".
  if (step.result.content.startsWith("Stopped by the user")) return "stopped";
  return step.result.status === "error" ? "refused" : "ok";
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
