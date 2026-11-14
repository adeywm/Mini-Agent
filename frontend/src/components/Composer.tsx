import { useEffect, useMemo, useRef } from "react";
import type { Connection } from "../api";
import { ProviderIcon } from "./Icons";
import PromptBar, { type PromptBarCommand, type PromptBarSource } from "./PromptBar";

interface Props {
  busy: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  autoFocusKey?: string | null;
  changesEnabled: boolean;
  connections: Connection[];
}

interface Template extends PromptBarCommand {
  /** The full request a slash command stands for. */
  prompt: (c: { gh?: Connection; aws?: Connection }) => string;
}

const TEMPLATES: Template[] = [
  { key: "commit", name: "/commit", description: "Review the latest commit", prompt: ({ gh }) => `Review the latest commit in my ${gh?.github_user ?? "GitHub"} repositories` },
  { key: "issues", name: "/issues", description: "Open issues and pull requests", prompt: ({ gh }) => `List open issues and pull requests for ${gh?.github_user ?? "my GitHub account"}` },
  { key: "ec2", name: "/ec2", description: "Running EC2 instances", prompt: ({ aws }) => `Which EC2 instances are running in ${aws?.aws_region ?? "my AWS account"}?` },
  { key: "s3", name: "/s3", description: "Buckets and their sizes", prompt: () => "Which S3 buckets do I have, and how big are the largest ones?" },
  { key: "costs", name: "/costs", description: "This month's AWS cost by service", prompt: () => "What has my AWS account cost so far this month, by service?" },
];

/** The message box: PromptBar (React Bits) with @ for accounts and / for ready-made requests.
 *  The model and effort pickers stay hidden: the model is chosen under Settings > Model. */
export function Composer({ busy, onSend, onStop, autoFocusKey, changesEnabled, connections }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const gh = connections.find((c) => c.kind === "github");
  const aws = connections.find((c) => c.kind === "aws");

  useEffect(() => {
    wrap.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
  }, [autoFocusKey]);

  const sources = useMemo<PromptBarSource[]>(
    () => connections.map((c) => ({
      key: c.id,
      name: c.name,
      description: c.kind === "github" ? `GitHub · ${c.github_user}` : `AWS · ${c.aws_profile ?? "default"} · ${c.aws_region}`,
      icon: <ProviderIcon kind={c.kind} size={14} />,
    })),
    [connections],
  );

  const send = (text: string) => {
    // A leading slash command expands to its full request; anything typed after it is kept.
    const match = /^(\/[\w-]+)\s*([\s\S]*)$/.exec(text);
    const template = match && TEMPLATES.find((t) => t.name === match[1]);
    const value = template ? [template.prompt({ gh, aws }), match[2]].filter(Boolean).join(" ") : text;
    if (value.trim()) onSend(value.trim());
  };

  return (
    <div className="composer-wrap" ref={wrap}>
      <PromptBar
        className="composer-bar"
        placeholder="Ask about your GitHub repos or AWS account… (@ for an account, / for a request)"
        sources={sources}
        commands={TEMPLATES}
        models={[]}
        efforts={[]}
        busy={busy}
        onSend={send}
        onStop={onStop}
        width={760}
        radius={12}
        maxRows={8}
        background="var(--surface)"
        color="var(--fg)"
        menuBackground="var(--surface)"
      />
      <p className="hint">Enter to send · Shift + Enter for a new line · {changesEnabled ? "Reads run automatically; changes wait for your approval" : "Add a GitHub or AWS account to start"}</p>
    </div>
  );
}
