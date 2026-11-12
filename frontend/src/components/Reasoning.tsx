import { Brain, ChevronRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { formatMs } from "../lib";

interface Props {
  text: string;
  /** True while the model is still reasoning (no answer yet). */
  thinking: boolean;
  ms?: number;
}

/** The model's own reasoning, when it exposes it: open and live while it thinks, then
 *  collapsed to one line ("Thought for 4.2 s") that can be expanded again. */
export function Reasoning({ text, thinking, ms }: Props) {
  const [open, setOpen] = useState<boolean | null>(null); // null = follow `thinking`
  const expanded = open ?? thinking;
  const body = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (thinking && body.current) body.current.scrollTop = body.current.scrollHeight;
  }, [text, thinking]);

  const label = thinking ? "Thinking" : ms !== undefined ? `Thought for ${formatMs(ms)}` : "Reasoning";
  return (
    <div className={`reasoning ${thinking ? "live" : ""} ${expanded ? "open" : ""}`}>
      <button className="reasoning-toggle" onClick={() => setOpen(!expanded)} aria-expanded={expanded}>
        <Brain size={14} aria-hidden />
        <span className={thinking ? "shimmer" : ""}>{label}</span>
        <ChevronRight size={14} className="chev" aria-hidden />
      </button>
      {expanded && (
        <div className="reasoning-body" ref={body}>
          {text}
        </div>
      )}
    </div>
  );
}
