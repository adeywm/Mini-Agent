import type { ToolResult } from "./ChatView";

function parse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

/** Find the first array of objects in a JSON value (e.g. Reservations, Buckets). */
function rows(value: unknown, depth = 0): Record<string, unknown>[] | null {
  if (Array.isArray(value) && value.length && value.every((v) => v && typeof v === "object")) {
    return value as Record<string, unknown>[];
  }
  if (value && typeof value === "object" && depth < 2) {
    for (const v of Object.values(value)) {
      const found = rows(v, depth + 1);
      if (found) return found;
    }
  }
  return null;
}

function cell(v: unknown): string {
  if (v === null || v === undefined) return "";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

function Table({ data }: { data: Record<string, unknown>[] }) {
  const cols = Array.from(new Set(data.flatMap((r) => Object.keys(r)))).slice(0, 8);
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.slice(0, 200).map((r, i) => (
            <tr key={i}>
              {cols.map((c) => (
                <td key={c}>{cell(r[c]).slice(0, 200)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ResultsPanel({ item }: { item: ToolResult | null }) {
  if (!item) {
    return (
      <aside className="results">
        <h3>Results</h3>
        <p className="muted small">Tool results show here. Click a tool step in the chat.</p>
      </aside>
    );
  }
  const { call, result } = item;
  const data = result ? parse(result.content) : undefined;
  const table = data !== undefined ? rows(data) : null;
  return (
    <aside className="results">
      <h3>{call.name.split("__").slice(1).join("__") || call.name}</h3>
      <p className="muted small">connection: {call.name.split("__")[0]}</p>
      <details>
        <summary>Arguments</summary>
        <pre>{JSON.stringify(call.args, null, 2)}</pre>
      </details>
      {!result && <p className="muted">Running…</p>}
      {result?.status === "error" && <p className="warn">{result.content}</p>}
      {result && result.status !== "error" && (
        <>
          {table && <Table data={table} />}
          <details open={!table}>
            <summary>Raw output</summary>
            <pre>{data !== undefined ? JSON.stringify(data, null, 2) : result.content}</pre>
          </details>
        </>
      )}
    </aside>
  );
}
