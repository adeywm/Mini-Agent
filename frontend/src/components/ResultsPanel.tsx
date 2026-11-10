import { ArrowDownUp, Ban, Check, ChevronRight, CircleHelp, Loader2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
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
/** Lift nested plain-object fields into dotted columns (e.g. commit.author.name). */
function liftNested(row: Row, prefix = "", depth = 0, out: Row = {}): Row {
  for (const [k, v] of Object.entries(row)) {
    const key = prefix ? `${prefix}.${k}` : k;
    const isObject = v !== null && typeof v === "object" && !Array.isArray(v);
    if (isObject && depth < 2) {
      liftNested(v as Row, key, depth + 1, out);
    } else {
      out[key] = v;
    }
  }
  return out;
}

function flatten(rows: Row[]): Row[] {
  const base = rows.every((r) => Array.isArray(r.Instances))
    ? rows.flatMap((r) => r.Instances as Row[])
    : rows;
  return base.map((r) => liftNested(r));
}

function cell(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string" && /^[0-9a-f]{40}$/.test(v)) return v.slice(0, 7); // commit SHA
  if (Array.isArray(v) && v.every((t) => t && typeof t === "object" && "Key" in t && "Value" in t)) {
    return (v as { Key: string; Value: string }[]).map((t) => `${t.Key}=${t.Value}`).join(", ");
  }
  if (typeof v === "object") {
    const o = v as Row;
    return String(o.Name ?? o.name ?? o.login ?? o.State ?? JSON.stringify(v));
  }
  return String(v);
}

const PREFERRED = ["Name", "name", "full_name", "title", "number", "state", "InstanceId",
  "InstanceType", "State.Name", "sha", "commit.message", "commit.author.name", "commit.author.date",
  "message", "login", "user.login", "description", "CreationDate", "updated_at", "html_url"];

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

function StateBadge({ step }: { step: ToolStep }) {
  const st = stepState(step);
  return (
    <span className={`badge ${st}`}>
      {st === "running" ? (
        <Loader2 size={12} className="spin" />
      ) : st === "ok" ? (
        <Check size={12} />
      ) : st === "stopped" ? (
        <CircleHelp size={12} />
      ) : (
        <Ban size={12} />
      )}
      {st === "stopped" ? "outcome unknown" : st}
    </span>
  );
}

/** Short, distinguishing description of a call (e.g. "repo: Mini-Agent · perPage: 1"). */
function argLine(args: Record<string, unknown>): string {
  return Object.entries(args)
    .filter(([, v]) => v !== null && v !== undefined && v !== "" && typeof v !== "object")
    .slice(0, 3)
    .map(([k, v]) => `${k}: ${String(v)}`)
    .join(" · ");
}

function ResultDetail({ step }: { step: ToolStep }) {
  const data =
    step.result && step.result.status !== "error" ? parseJson(step.result.content) : undefined;
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
    <div className="result-detail">
      <details className="args">
        <summary>Arguments</summary>
        <pre>{JSON.stringify(step.call.args, null, 2)}</pre>
      </details>
      {!step.result && <p className="muted small">Running…</p>}
      {step.result?.status === "error" && (
        <div className={stepState(step) === "stopped" ? "unknown-outcome" : "refusal"}>
          {step.result.content}
        </div>
      )}
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
  );
}

interface Props {
  steps: ToolStep[];
  selectedId: string | null;
  connections: Connection[];
  onSelect: (id: string) => void;
  onClose: () => void;
}

export function ResultsPanel({ steps, selectedId, connections, onSelect, onClose }: Props) {
  // Cards stack in call order. The selected card opens and scrolls into view; several can be
  // open at once so results can be compared.
  const [openIds, setOpenIds] = useState<Set<string>>(new Set());
  const cardRefs = useRef<Record<string, HTMLElement | null>>({});

  useEffect(() => {
    if (!selectedId) return;
    setOpenIds((s) => new Set(s).add(selectedId));
    const el = cardRefs.current[selectedId];
    if (el) requestAnimationFrame(() => el.scrollIntoView({ behavior: "smooth", block: "start" }));
  }, [selectedId]);

  const toggle = (id: string) => {
    setOpenIds((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    onSelect(id);
  };

  return (
    <aside className="results" aria-label="Results">
      <header className="results-head">
        <strong>Results</strong>
        <span className="muted small">
          {steps.length} step{steps.length === 1 ? "" : "s"}
        </span>
        <span className="spacer" />
        {openIds.size > 0 && (
          <button className="ghost xs" onClick={() => setOpenIds(new Set())}>
            Collapse all
          </button>
        )}
        <button className="ghost icon-only" onClick={onClose} aria-label="Close results">
          <X size={16} />
        </button>
      </header>
      <div className="result-stack">
        {steps.length === 0 && (
          <p className="muted small pad">Tool results from this chat appear here.</p>
        )}
        {steps.map((s, i) => {
          const { connection, tool } = splitTool(s.call.name);
          const isOpen = openIds.has(s.call.id);
          return (
            <section
              key={s.call.id}
              ref={(el) => {
                cardRefs.current[s.call.id] = el;
              }}
              className={`result-card ${isOpen ? "open" : ""} ${s.call.id === selectedId ? "selected" : ""}`}
            >
              <button className="card-head" onClick={() => toggle(s.call.id)} aria-expanded={isOpen}>
                <span className="step-no">{i + 1}</span>
                <ChevronRight size={14} className="chev" />
                <ProviderIcon kind={kindOf(connection, connections)} size={14} />
                <span className="card-title">
                  <span className="card-name">{humanTool(tool)}</span>
                  <span className="card-args">{argLine(s.call.args) || connection}</span>
                </span>
                <span className="card-meta">
                  <StateBadge step={s} />
                  {formatMs(s.ms) && <span className="muted small">{formatMs(s.ms)}</span>}
                </span>
              </button>
              {isOpen && <ResultDetail step={s} />}
            </section>
          );
        })}
      </div>
    </aside>
  );
}
