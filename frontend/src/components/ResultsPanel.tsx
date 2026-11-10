import { ArrowDownUp, Ban, Check, Loader2, X } from "lucide-react";
import { useMemo, useState } from "react";
import type { Connection } from "../api";
import { formatMs, humanTool, kindOf, parseJson, splitTool, stepState, type ToolStep } from "../lib";
import { ProviderIcon } from "./Icons";
import { CopyButton } from "./Markdown";

type Row = Record<string, unknown>;

/** The first array of objects in a JSON value (e.g. Reservations, Buckets, commits). */
function findRows(value: unknown, depth = 0): Row[] | null {
  if (Array.isArray(value) && value.length && value.every((v) => v && typeof v === "object" && !Array.isArray(v))) {
    return value as Row[];
  }
  if (value && typeof value === "object" && depth < 3) {
    for (const v of Object.values(value)) {
      const found = findRows(v, depth + 1);
      if (found) return found;
    }
  }
  return null;
}

/** EC2 nests instances inside reservations: flatten them for the table. */
function flatten(rows: Row[]): Row[] {
  if (rows.every((r) => Array.isArray(r.Instances))) return rows.flatMap((r) => r.Instances as Row[]);
  return rows;
}

function cell(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (Array.isArray(v) && v.every((t) => t && typeof t === "object" && "Key" in t && "Value" in t)) {
    return (v as { Key: string; Value: string }[]).map((t) => `${t.Key}=${t.Value}`).join(", ");
  }
  if (typeof v === "object") {
    const o = v as Row;
    return String(o.Name ?? o.name ?? o.login ?? o.State ?? JSON.stringify(v));
  }
  return String(v);
}

const PREFERRED = ["Name", "name", "title", "number", "state", "InstanceId", "InstanceType", "State",
  "sha", "message", "login", "CreationDate", "html_url"];

function Table({ rows }: { rows: Row[] }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ col: string; dir: 1 | -1 } | null>(null);
  const cols = useMemo(() => {
    const all = Array.from(new Set(rows.flatMap((r) => Object.keys(r))));
    const simple = all.filter((c) => rows.some((r) => r[c] !== null && typeof r[c] !== "object") || c === "Tags" || c === "State");
    simple.sort((a, b) => {
      const ia = PREFERRED.indexOf(a), ib = PREFERRED.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    return simple.slice(0, 7);
  }, [rows]);
  const shown = useMemo(() => {
    const needle = q.toLowerCase();
    let list = rows.filter((r) => !needle || cols.some((c) => cell(r[c]).toLowerCase().includes(needle)));
    if (sort) list = [...list].sort((a, b) => cell(a[sort.col]).localeCompare(cell(b[sort.col]), undefined, { numeric: true }) * sort.dir);
    return list;
  }, [rows, cols, q, sort]);

  return (
    <div className="data-table">
      <div className="table-tools">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Filter ${rows.length} rows`} aria-label="Filter rows" />
        <span className="muted small">{shown.length} shown</span>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {cols.map((c) => (
                <th key={c}>
                  <button className="th" onClick={() => setSort(sort?.col === c ? { col: c, dir: sort.dir === 1 ? -1 : 1 } : { col: c, dir: 1 })}>
                    {c} <ArrowDownUp size={11} />
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.slice(0, 300).map((r, i) => (
              <tr key={i}>
                {cols.map((c) => (
                  <td key={c} title={cell(r[c])}>{cell(r[c]).slice(0, 120)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

interface DiffFile { filename: string; status?: string; additions?: number; deletions?: number; patch?: string }

function Diff({ files }: { files: DiffFile[] }) {
  return (
    <div className="diff">
      {files.map((f) => (
        <details key={f.filename} open={files.length <= 5}>
          <summary>
            <span className="diff-name">{f.filename}</span>
            <span className="add">+{f.additions ?? 0}</span> <span className="del">−{f.deletions ?? 0}</span>
          </summary>
          {f.patch ? (
            <pre className="diff-body">
              {f.patch.split("\n").map((line, i) => (
                <span key={i} className={line.startsWith("+") ? "l-add" : line.startsWith("-") ? "l-del" : line.startsWith("@@") ? "l-hunk" : ""}>
                  {line + "\n"}
                </span>
              ))}
            </pre>
          ) : (
            <p className="muted small pad">No patch shown (binary or too large).</p>
          )}
        </details>
      ))}
    </div>
  );
}

type View = "table" | "diff" | "json" | "text";

interface Props {
  steps: ToolStep[];
  selectedId: string | null;
  connections: Connection[];
  onSelect: (id: string) => void;
  onClose: () => void;
}

export function ResultsPanel({ steps, selectedId, connections, onSelect, onClose }: Props) {
  const step = steps.find((s) => s.call.id === selectedId) ?? steps[steps.length - 1];
  const data = step?.result && step.result.status !== "error" ? parseJson(step.result.content) : undefined;
  const files = (data as { files?: DiffFile[] } | undefined)?.files;
  const hasDiff = Array.isArray(files) && files.some((f) => f && f.patch);
  const rows = data !== undefined ? findRows(data) : null;
  const views: View[] = [
    ...(hasDiff ? (["diff"] as View[]) : []),
    ...(rows ? (["table"] as View[]) : []),
    ...(data !== undefined ? (["json"] as View[]) : (["text"] as View[])),
  ];
  const [picked, setPicked] = useState<View | null>(null);
  const view = picked && views.includes(picked) ? picked : views[0];

  return (
    <aside className="results" aria-label="Results">
      <header className="results-head">
        <strong>Results</strong>
        <button className="ghost icon-only" onClick={onClose} aria-label="Close results">
          <X size={16} />
        </button>
      </header>
      {steps.length === 0 ? (
        <p className="muted small pad">Tool results from this chat appear here.</p>
      ) : (
        <>
          <div className="result-tabs" role="tablist">
            {steps.map((s) => {
              const { connection, tool } = splitTool(s.call.name);
              const st = stepState(s);
              return (
                <button key={s.call.id} role="tab" aria-selected={s === step} className={`rtab ${s === step ? "active" : ""} ${st}`}
                  onClick={() => { setPicked(null); onSelect(s.call.id); }}>
                  <ProviderIcon kind={kindOf(connection, connections)} size={12} />
                  {humanTool(tool)}
                </button>
              );
            })}
          </div>
          {step && (
            <div className="result-body">
              <div className="result-meta">
                <span className={`badge ${stepState(step)}`}>
                  {stepState(step) === "running" ? <Loader2 size={12} className="spin" /> : stepState(step) === "ok" ? <Check size={12} /> : <Ban size={12} />}
                  {stepState(step)}
                </span>
                <span className="muted small">{splitTool(step.call.name).connection} {formatMs(step.ms) && `· ${formatMs(step.ms)}`}</span>
              </div>
              <details className="args">
                <summary>Arguments</summary>
                <pre>{JSON.stringify(step.call.args, null, 2)}</pre>
              </details>
              {step.result?.status === "error" && <div className="refusal">{step.result.content}</div>}
              {step.result && step.result.status !== "error" && (
                <>
                  <div className="view-switch">
                    {views.map((v) => (
                      <button key={v} className={v === view ? "active" : ""} onClick={() => setPicked(v)}>
                        {v === "json" ? "JSON" : v[0].toUpperCase() + v.slice(1)}
                      </button>
                    ))}
                    <span className="spacer" />
                    <CopyButton text={step.result.content} />
                  </div>
                  {view === "table" && rows && <Table rows={flatten(rows)} />}
                  {view === "diff" && files && <Diff files={files} />}
                  {view === "json" && <pre className="raw">{JSON.stringify(data, null, 2)}</pre>}
                  {view === "text" && <pre className="raw">{step.result.content}</pre>}
                </>
              )}
            </div>
          )}
        </>
      )}
    </aside>
  );
}
