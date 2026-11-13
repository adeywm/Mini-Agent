import { AlertTriangle, Check, Loader2, LogIn, Plug, RotateCw, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Approval, type ChatNotice, type Connection, type Message } from "../api";
import { buildItems } from "../lib";
import { Composer } from "./Composer";
import { CopyButton, Markdown } from "./Markdown";
import { ToolSteps } from "./ToolSteps";
import { ApprovalCards } from "./ApprovalCards";
import { Reasoning } from "./Reasoning";

interface Props {
  chatId: string | null;
  messages: Message[];
  timings: Record<string, number>;
  notices: ChatNotice[];
  busy: boolean;
  connections: Connection[];
  changesEnabled: boolean;
  approvals: Approval[];
  onDecide: (approval: Approval, decision: "approve" | "reject", message?: string) => void;
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
  const awaitingIds = useMemo(() => new Set(props.approvals.map((a) => a.tool_call_id)), [props.approvals]);
  const lastUserText = useMemo(() => [...messages].reverse().find((m) => m.role === "user")?.content, [messages]);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [items, notices, busy, props.approvals]);

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
                  <Plug size={15} /> Connect GitHub or AWS
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
                    ) : s.kind === "reasoning" ? (
                      <Reasoning key={s.id} text={s.text} thinking={!!s.thinking} ms={s.ms} />
                    ) : (
                      <ToolSteps
                        key={s.id}
                        steps={s.steps}
                        connections={connections}
                        selectedId={selectedToolId}
                        onOpen={props.onOpenTool}
                        awaiting={awaitingIds}
                      />
                    ),
                  )}
                </div>
              </div>
            ),
          )}
          <ApprovalCards approvals={props.approvals} busy={busy} onDecide={props.onDecide} />
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
          {notices.map((notice, i) => notice.type === "auth_required" ? (
            <AuthNotice key={`n${i}`} notice={notice} onRetry={lastUserText ? () => props.onSend(lastUserText) : undefined} />
          ) : (
            <div key={`n${i}`} className="notice" role={notice.type === "error" ? "alert" : "status"}>
              {notice.content}
            </div>
          ))}
        </div>
      </div>
      <Composer busy={busy} onSend={props.onSend} onStop={props.onStop} autoFocusKey={chatId} changesEnabled={props.changesEnabled} />
    </section>
  );
}

type AuthNoticeData = Extract<ChatNotice, { type: "auth_required" }>;

/** Expired AWS sign-in in a chat: sign in from here, wait for it, then retry the message. */
function AuthNotice({ notice, onRetry }: { notice: AuthNoticeData; onRetry?: () => void }) {
  const command = (notice.method === "sso" ? "aws sso login" : "aws login") + (notice.profile ? ` --profile ${notice.profile}` : "");
  const [phase, setPhase] = useState<"idle" | "starting" | "waiting" | "done">("idle");
  const [error, setError] = useState("");

  useEffect(() => {
    if (phase !== "waiting") return;
    const started = Date.now();
    const timer = setInterval(async () => {
      if (Date.now() - started > 5 * 60 * 1000) { setPhase("idle"); return; }
      try {
        if ((await api.awsLoginStatus(notice.profile)).state === "ok") setPhase("done");
      } catch { /* keep waiting */ }
    }, 3000);
    return () => clearInterval(timer);
  }, [phase, notice.profile]);

  const signIn = async () => {
    setError("");
    setPhase("starting");
    try {
      await api.awsLogin(notice.profile);
      setPhase("waiting");
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      if (message.includes("already signed in")) setPhase("done");
      else { setError(message); setPhase("idle"); }
    }
  };

  if (phase === "done") {
    return (
      <div className="notice auth-notice done" role="status">
        <div className="auth-notice-heading"><Check size={17} aria-hidden="true" /><strong>Signed in to AWS</strong></div>
        <p>{notice.profile ?? "Default profile"} is signed in again.</p>
        {onRetry && <button className="primary" onClick={onRetry}><RotateCw size={14} /> Retry message</button>}
      </div>
    );
  }
  return (
    <div className="notice auth-notice" role="status">
      <div className="auth-notice-heading"><AlertTriangle size={17} aria-hidden="true" /><strong>AWS sign-in expired</strong></div>
      <p>{notice.sources.join(" and ")} {notice.sources.length === 1 ? "needs" : "need"} a fresh sign-in (profile <strong>{notice.profile ?? "default"}</strong>).</p>
      <div className="auth-command">
        {phase === "waiting" ? (
          <span className="auth-waiting"><Loader2 size={14} className="spin" /> Finish signing in in your browser. Waiting…</span>
        ) : (
          <button className="primary" disabled={phase === "starting"} onClick={signIn}>
            {phase === "starting" ? <Loader2 size={14} className="spin" /> : <LogIn size={14} />} Sign in
          </button>
        )}
        <span className="muted small">or run</span>
        <code>{command}</code>
        <CopyButton text={command} label="Copy" />
      </div>
      {error && <p className="small">{error}</p>}
      <details><summary>Technical details</summary><span>Error: {notice.errorTypes.join(", ")}</span></details>
    </div>
  );
}
