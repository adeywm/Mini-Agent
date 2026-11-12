import { ArrowUp, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";

interface Props {
  busy: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  autoFocusKey?: string | null;
  changesEnabled: boolean;
}

export function Composer({ busy, onSend, onStop, autoFocusKey, changesEnabled }: Props) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    ref.current?.focus();
  }, [autoFocusKey]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [text]);

  const submit = () => {
    const value = text.trim();
    if (!value || busy) return;
    setText("");
    onSend(value);
  };

  return (
    <div className="composer-wrap">
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <textarea
          ref={ref}
          value={text}
          rows={1}
          placeholder="Ask about your GitHub repos or AWS account…"
          aria-label="Message"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
        />
        {busy ? (
          <button type="button" className="send stop" onClick={onStop} aria-label="Stop">
            <Square size={14} fill="currentColor" />
          </button>
        ) : (
          <button type="submit" className="send" disabled={!text.trim()} aria-label="Send">
            <ArrowUp size={18} />
          </button>
        )}
      </form>
      <p className="hint">Enter to send · Shift + Enter for a new line · {changesEnabled ? "Reads run automatically; changes wait for your approval" : "Read only: raise a connection to L2 or L3 to allow changes"}</p>
    </div>
  );
}
