import { Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import type { Connection, Message } from "../api";
import { buildItems } from "../lib";
import { Composer } from "./Composer";
import { ProviderIcon } from "./Icons";
import { Markdown } from "./Markdown";
import { ToolSteps } from "./ToolSteps";

interface Props {
  chatId: string | null;
  messages: Message[];
  timings: Record<string, number>;
  notices: string[];
  busy: boolean;
  connections: Connection[];
  selectedToolId: string | null;
  onOpenTool: (id: string) => void;
  onSend: (text: string) => void;
  onStop: () => void;
  onConnections: () => void;
}

function suggestions(connections: Connection[]): string[] {
  const gh = connections.find((c) => c.kind === "github");
  const aws = connections.find((c) => c.kind === "aws");
  const list: string[] = [];
  if (gh) {
    list.push(`Review the latest commit in my ${gh.github_user} repositories`);
    list.push(`List open issues and pull requests for ${gh.github_user}`);
  }
  if (aws) {
    list.push(`Which EC2 instances are running in ${aws.aws_region}?`);
    list.push("Which S3 buckets do I have, and how big are the largest ones?");
  }
  return list.slice(0, 4);
}

export function ChatView(props: Props) {
  const { chatId, messages, timings, notices, busy, connections, selectedToolId } = props;
  const items = useMemo(() => buildItems(messages, timings), [messages, timings]);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [items, notices, busy]);

  const waiting =
    busy &&
    !messages.some((m, i) => i === messages.length - 1 && m.role === "assistant" && m.streaming);

  return (
    <section className="chat">
      <div
        className="scroller"
        ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
      >
        <div className="thread">
          {items.length === 0 && (
            <div className="welcome">
              <div className="welcome-icon">
                <Sparkles size={26} />
              </div>
              <h1>How can I help?</h1>
              <p className="muted">
                I can read and work with your GitHub repositories and AWS account, and I ask before
                anything risky.
              </p>
              {connections.length === 0 ? (
                <button className="primary" onClick={props.onConnections}>
                  Connect GitHub or AWS
                </button>
              ) : (
                <div className="suggestions">
                  {suggestions(connections).map((s) => (
                    <button key={s} className="suggestion" onClick={() => props.onSend(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              )}
              {connections.length > 0 && (
                <div className="welcome-conns">
                  {connections.map((c) => (
                    <span key={c.id} className="pill">
                      <ProviderIcon kind={c.kind} size={12} /> {c.name}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
          {items.map((item, i) =>
            item.kind === "user" ? (
              <div key={i} className="row user">
                <div className="bubble">{item.text}</div>
              </div>
            ) : (
              <div key={i} className="row assistant">
                <div className="avatar" aria-hidden>
                  <Sparkles size={15} />
                </div>
                <div className="turn">
                  {item.segments.map((s) =>
                    s.kind === "text" ? (
                      <div key={s.id} className={s.streaming ? "streaming" : ""}>
                        <Markdown text={s.text} />
                      </div>
                    ) : (
                      <ToolSteps
                        key={s.id}
                        steps={s.steps}
                        connections={connections}
                        selectedId={selectedToolId}
                        onOpen={props.onOpenTool}
                      />
                    ),
                  )}
                </div>
              </div>
            ),
          )}
          {waiting && (
            <div className="row assistant">
              <div className="avatar" aria-hidden>
                <Sparkles size={15} />
              </div>
              <div className="typing" aria-label="Thinking">
                <span />
                <span />
                <span />
              </div>
            </div>
          )}
          {notices.map((n, i) => (
            <div key={`n${i}`} className="notice">
              {n}
            </div>
          ))}
        </div>
      </div>
      <Composer busy={busy} onSend={props.onSend} onStop={props.onStop} autoFocusKey={chatId} />
    </section>
  );
}
