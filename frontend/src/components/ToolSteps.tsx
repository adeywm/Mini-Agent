import { Ban, Check, ChevronRight, Loader2 } from "lucide-react";
import { useState } from "react";
import type { Connection } from "../api";
import { formatMs, humanTool, kindOf, splitTool, stepState, type ToolStep } from "../lib";
import { ProviderIcon } from "./Icons";

function argSummary(args: Record<string, unknown>): string {
  const parts = Object.entries(args)
    .filter(([, v]) => v !== null && v !== undefined && v !== "" && typeof v !== "object")
    .slice(0, 3)
    .map(([k, v]) => `${k}: ${String(v)}`);
  return parts.join(" · ");
}

interface Props {
  steps: ToolStep[];
  connections: Connection[];
  selectedId: string | null;
  onOpen: (id: string) => void;
}

export function ToolSteps({ steps, connections, selectedId, onOpen }: Props) {
  const running = steps.some((s) => !s.result);
  const refused = steps.filter((s) => stepState(s) === "refused").length;
  const [open, setOpen] = useState(false);
  const expanded = open || running;
  const kinds = Array.from(new Set(steps.map((s) => kindOf(splitTool(s.call.name).connection, connections))));

  return (
    <div className={`tool-steps ${expanded ? "open" : ""}`}>
      <button className="tool-summary" onClick={() => setOpen(!open)} aria-expanded={expanded}>
        <ChevronRight size={14} className="chev" />
        <span className="kinds">
          {kinds.map((k, i) => (
            <ProviderIcon key={i} kind={k} />
          ))}
        </span>
        {running ? (
          <span>
            Working <Loader2 size={13} className="spin" />
          </span>
        ) : (
          <span>
            Used {steps.length} tool{steps.length > 1 ? "s" : ""}
            {refused > 0 && <span className="refused-note"> · {refused} refused</span>}
          </span>
        )}
      </button>
      {expanded && (
        <ol className="step-list">
          {steps.map((s) => {
            const { connection, tool } = splitTool(s.call.name);
            const state = stepState(s);
            return (
              <li key={s.call.id}>
                <button
                  className={`step ${state} ${s.call.id === selectedId ? "selected" : ""}`}
                  onClick={() => onOpen(s.call.id)}
                  title="Open in results"
                >
                  <span className={`state-icon ${state}`}>
                    {state === "running" ? (
                      <Loader2 size={13} className="spin" />
                    ) : state === "ok" ? (
                      <Check size={13} />
                    ) : (
                      <Ban size={13} />
                    )}
                  </span>
                  <ProviderIcon kind={kindOf(connection, connections)} size={13} />
                  <span className="step-name">{humanTool(tool)}</span>
                  <span className="step-args">{argSummary(s.call.args)}</span>
                  <span className="step-meta">{formatMs(s.ms)}</span>
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
