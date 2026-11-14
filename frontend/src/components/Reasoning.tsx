import { useMemo } from "react";
import ThoughtLine from "./ThoughtLine";

interface Props {
  text: string;
  /** True while the model is still reasoning (no answer yet). */
  thinking: boolean;
  ms?: number;
}

/** The model's own reasoning, when it exposes it, as a ThoughtLine (React Bits): open and
 *  live while it thinks, one paragraph per step, then collapsed to "Thought for 4.2s". */
export function Reasoning({ text, thinking, ms }: Props) {
  const steps = useMemo(
    () => text.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean),
    [text],
  );
  return (
    <ThoughtLine
      className="reasoning-line"
      label="Thinking…"
      doneLabel={ms === undefined ? "Reasoning" : "Thought for"}
      working={thinking}
      elapsed={thinking ? undefined : ms === undefined ? undefined : ms / 1000}
      showTimer={thinking || ms !== undefined}
      steps={steps}
      fontSize={13}
      color="var(--muted)"
      glyphColor="var(--fg-soft)"
    />
  );
}
