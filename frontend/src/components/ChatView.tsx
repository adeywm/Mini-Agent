import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message, ToolCall } from "../api";

export interface ToolResult {
  call: ToolCall;
  result?: Extract<Message, { role: "tool" }>;
}

interface Props {
  messages: Message[];
  notices: string[];
  busy: boolean;
  hasConnections: boolean;
  selectedToolId: string | null;
  onSelectTool: (id: string) => void;
  onSend: (text: string) => void;
}

function toolLabel(name: string): string {
  const [conn, ...rest] = name.split("__");
  return rest.length ? `${conn} · ${rest.join("__").replace(/^aws___/, "")}` : name;
}

export function ChatView(props: Props) {
  const { messages, notices, busy, hasConnections, selectedToolId, onSelectTool, onSend } = props;
  const [text, setText] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const results = new Map(
    messages.filter((m) => m.role === "tool").map((m) => [m.tool_call_id, m] as const),
  );

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  const submit = () => {
    const value = text.trim();
    if (!value || busy) return;
    setText("");
    onSend(value);
  };

  return (
    <section className="chat">
      <div className="messages">
        {messages.length === 0 && (
          <div className="empty">
            <h2>What should I look at?</h2>
            <p className="muted">
              e.g. “Review the latest commit in adeywm/Mini-Agent” or “How much space is my S3
              bucket using?”
            </p>
            {!hasConnections && (
              <p className="warn">Add a GitHub or AWS connection first (Connections).</p>
            )}
          </div>
        )}
        {messages.map((m, i) => {
          if (m.role === "user")
            return (
              <div key={i} className="msg user">
                {m.content}
              </div>
            );
          if (m.role === "tool") return null;
          return (
            <div key={i} className="msg assistant">
              {m.content && (
                <div className="md">
                  <Markdown remarkPlugins={[remarkGfm]}>{m.content}</Markdown>
                </div>
              )}
              {m.tool_calls.map((c) => {
                const r = results.get(c.id);
                const state = !r ? "running" : r.status === "error" ? "refused" : "ok";
                return (
                  <button
                    key={c.id}
                    className={`tool-chip ${state} ${c.id === selectedToolId ? "selected" : ""}`}
                    onClick={() => onSelectTool(c.id)}
                    title="Show in results panel"
                  >
                    <span className="dot" /> {toolLabel(c.name)}
                    <span className="muted small"> {state}</span>
                  </button>
                );
              })}
            </div>
          );
        })}
        {notices.map((n, i) => (
          <div key={`n${i}`} className="notice">
            {n}
          </div>
        ))}
        {busy && <div className="muted small thinking">Working…</div>}
        <div ref={bottom} />
      </div>
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <textarea
          value={text}
          placeholder="Ask anything about your GitHub or AWS…"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          rows={2}
          aria-label="Message"
        />
        <button className="primary" type="submit" disabled={busy || !text.trim()}>
          Send
        </button>
      </form>
    </section>
  );
}
