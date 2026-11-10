// Client for agents-server. Every change sends the x-mini-agent header (CSRF protection).

export type Level = 1 | 2 | 3;

export interface Connection {
  id: string;
  kind: "aws" | "github";
  name: string;
  max_level: Level;
  aws_profile: string | null;
  aws_region: string | null;
  github_user: string | null;
  created_at: string;
}

export type NewConnection = Omit<Connection, "id" | "created_at">;

export interface Chat {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
}

export type UserMessage = { role: "user"; content: string };
export type AssistantMessage = {
  role: "assistant";
  id: string;
  content: string;
  tool_calls: ToolCall[];
  streaming?: boolean;
};
export type ToolMessage = {
  role: "tool";
  tool_call_id: string;
  name: string;
  status: string;
  content: string;
};
export type Message = UserMessage | AssistantMessage | ToolMessage;

export type StreamEvent =
  | { type: "token"; id: string; text: string }
  | { type: "message"; message: Message }
  | { type: "notice" | "error"; content: string }
  | { type: "done" };

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const resp = await fetch(path, {
    method,
    headers: { "content-type": "application/json", "x-mini-agent": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const data = await resp.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return (resp.status === 204 ? undefined : await resp.json()) as T;
}

export const api = {
  chats: () => call<Chat[]>("GET", "/api/chats"),
  newChat: () => call<Chat>("POST", "/api/chats"),
  renameChat: (id: string, title: string) => call<Chat>("PATCH", `/api/chats/${id}`, { title }),
  deleteChat: (id: string) => call<void>("DELETE", `/api/chats/${id}`),
  messages: (id: string) => call<Message[]>("GET", `/api/chats/${id}/messages`),
  connections: () => call<Connection[]>("GET", "/api/connections"),
  addConnection: (c: NewConnection) => call<Connection>("POST", "/api/connections", c),
  deleteConnection: (id: string) => call<void>("DELETE", `/api/connections/${id}`),
};

/** POST a message and yield the server-sent events as they arrive. */
export async function* send(
  chatId: string,
  content: string,
  signal: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const resp = await fetch(`/api/chats/${chatId}/messages`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-mini-agent": "1" },
    body: JSON.stringify({ content }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    const data = await resp.json().catch(() => ({ detail: resp.statusText }));
    yield { type: "error", content: String(data.detail) };
    return;
  }
  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      if (chunk.startsWith("data: ")) yield JSON.parse(chunk.slice(6)) as StreamEvent;
    }
  }
}
