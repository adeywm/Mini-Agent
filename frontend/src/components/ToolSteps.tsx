import { ChevronRight, Loader2 } from "lucide-react";
import { useState } from "react";
import type { Connection } from "../api";
import { formatMs, humanTool, kindOf, splitTool, stepLabel, stepState, type ToolStep } from "../lib";
import CallChip, { type CallChipStatus } from "./CallChip";
import { ProviderIcon } from "./Icons";

/** CallChip shows four states; ours has more, so map them (the label beside it says which). */
const CHIP: Record<string, CallChipStatus> = { running: "running", ok: "done", awaiting: "idle", stopped: "idle" };

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
  /** Tool call ids waiting for the user's approval. */
  awaiting?: ReadonlySet<string>;
}

export function ToolSteps({ steps: given, connections, selectedId, onOpen, awaiting }: Props) {
  const steps = given.map((s) => (awaiting?.has(s.call.id) && !s.result ? { ...s, awaiting: true } : s));
  const running = steps.some((s) => stepState(s) === "running");
  const waiting = steps.filter((s) => stepState(s) === "awaiting").length;
  const rejected = steps.filter((s) => stepState(s) === "rejected").length;
  const refused = steps.filter((s) => stepState(s) === "refused").length;
  const errors = steps.filter((s) => stepState(s) === "error").length;
  const stopped = steps.filter((s) => stepState(s) === "stopped").length;
  const [open, setOpen] = useState(true);
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
            {refused > 0 && <span className="refused-note"> · {refused} blocked</span>}
            {errors > 0 && <span className="refused-note"> · {errors} error{errors === 1 ? "" : "s"}</span>}
            {stopped > 0 && <span className="stopped-note"> · {stopped} stopped, outcome unknown</span>}
            {waiting > 0 && <span className="stopped-note"> · {waiting} awaiting approval</span>}
            {rejected > 0 && <span className="refused-note"> · {rejected} not run</span>}
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
                  <CallChip
                    className="step-chip"
                    icon={<ProviderIcon kind={kindOf(connection, connections)} size={13} />}
                    name={humanTool(tool)}
                    argument={argSummary(s.call.args)}
                    status={CHIP[state] ?? "error"}
                    showTimer={false}
                    size={30}
                    radius={6}
                    surfaceColor="transparent"
                    doneColor="var(--ok)"
                    errorColor="var(--bad)"
                    washOpacity={0.08}
                    shake={0}
                  />
                  <span className={`step-status ${state}`}>{stepLabel(s)}</span>
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
