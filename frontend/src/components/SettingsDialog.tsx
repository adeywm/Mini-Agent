import { BookOpen, Brain, Cpu, Monitor, Moon, Palette, Plug, Server, Sun, X } from "lucide-react";
import { Fragment, useEffect, type ReactNode } from "react";
import type { Connection, Level, ModelSelection, NewConnection } from "../api";
import type { Prefs } from "../lib";
import { ConnectionsSettings } from "./ConnectionsSettings";
import { ModelSettings } from "./ModelSettings";
import { pageTheme, SpotlightCard } from "./SpotlightCard";
import SquishSwitch from "./SquishSwitch";

export type SettingsTab = "connections" | "model" | "appearance" | "mcp" | "knowledge" | "learning";
export type Theme = "light" | "dark";

interface Props {
  tab: SettingsTab;
  onTab: (tab: SettingsTab) => void;
  onClose: () => void;
  connections: Connection[];
  onAdd: (c: NewConnection) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onSetLevel: (id: string, level: Level) => Promise<void>;
  model: ModelSelection | null;
  modelError: string | null;
  loadingModel: boolean;
  onRetryModel: () => void;
  onSaveModel: (selection: ModelSelection) => Promise<void>;
  theme: Theme;
  onTheme: (theme: Theme) => void;
  prefs: Prefs;
  onPrefs: (prefs: Prefs) => void;
}

type Tab = { id: SettingsTab; label: string; icon: ReactNode; title: string; soon?: boolean };

const TABS: Tab[] = [
  { id: "connections", label: "Connections", icon: <Plug size={15} />, title: "Connections" },
  { id: "model", label: "Model", icon: <Cpu size={15} />, title: "Bedrock model" },
  { id: "appearance", label: "Preferences", icon: <Palette size={15} />, title: "Preferences" },
  { id: "mcp", label: "MCP servers", icon: <Server size={15} />, title: "MCP servers", soon: true },
  { id: "knowledge", label: "Knowledge base", icon: <BookOpen size={15} />, title: "Knowledge base", soon: true },
  { id: "learning", label: "Learning", icon: <Brain size={15} />, title: "Learning from feedback", soon: true },
];

/** Planned features (README "Future scope"), shown so users know what is coming. */
const SOON: Record<string, { intro: string; points: string[]; action: string }> = {
  mcp: {
    intro: "Connect any MCP server, local or remote, next to GitHub and AWS.",
    points: [
      "Add a server by local command or HTTP URL.",
      "Its tools need your approval until you mark them read-only.",
      "Tool descriptions from a server are treated as untrusted text.",
    ],
    action: "Add MCP server",
  },
  knowledge: {
    intro: "Let the assistant search your own notes, docs and runbooks.",
    points: [
      "Point it at a local folder; it is indexed on this machine.",
      "The assistant gets a \"search my notes\" tool.",
      "Found text is used as data, never as instructions.",
    ],
    action: "Choose a folder",
  },
  learning: {
    intro: "The assistant adapts to you from your feedback.",
    points: [
      "Thumbs up or down, with an optional note, on each answer.",
      "Corrections become preferences used in later chats.",
      "See and delete everything it learned, here.",
    ],
    action: "Review what it learned",
  },
};

/** Every setting in one place: accounts, the Bedrock model and the theme. */
export function SettingsDialog(props: Props) {
  const { tab, onTab, onClose } = props;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const current = TABS.find((t) => t.id === tab)!;
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog settings" role="dialog" aria-modal="true" aria-labelledby="settings-title">
        <nav className="settings-tabs" role="tablist" aria-label="Settings">
          <span className="settings-heading">Settings</span>
          {TABS.map((t, i) => (<Fragment key={t.id}>
            {t.soon && !TABS[i - 1]?.soon && <span className="settings-group">Coming soon</span>}
            <button
              role="tab"
              aria-selected={tab === t.id}
              className={`settings-tab${tab === t.id ? " active" : ""}`}
              onClick={() => onTab(t.id)}
            >
              {t.icon} {t.label}
              {t.soon && <span className="soon-badge">Soon</span>}
            </button>
          </Fragment>))}
        </nav>
        <div className="settings-pane" role="tabpanel">
          <header>
            <h2 id="settings-title">{current.title}</h2>
            <button className="ghost icon-only" aria-label="Close settings" onClick={onClose}>
              <X size={18} />
            </button>
          </header>
          {tab === "connections" && (
            <ConnectionsSettings
              connections={props.connections}
              onAdd={props.onAdd}
              onDelete={props.onDelete}
              onSetLevel={props.onSetLevel}
            />
          )}
          {tab === "model" && (props.model ? (
            <ModelSettings current={props.model} onSave={props.onSaveModel} />
          ) : (
            <>
              {props.loadingModel ? <p className="muted">Loading model settings…</p> : <>
                <p className="error-text" role="alert">Could not load model settings. {props.modelError}</p>
                <p className="muted small">Check that the backend is running, then retry.</p>
              </>}
              <div className="pane-actions">
                <button className="primary" disabled={props.loadingModel} onClick={props.onRetryModel}>Retry</button>
              </div>
            </>
          ))}
          {tab === "appearance" && <Appearance theme={props.theme} onTheme={props.onTheme} prefs={props.prefs} onPrefs={props.onPrefs} />}
          {SOON[tab] && <ComingSoon {...SOON[tab]} />}
        </div>
      </div>
    </div>
  );
}

function Appearance({ theme, onTheme, prefs, onPrefs }: { theme: Theme; onTheme: (theme: Theme) => void; prefs: Prefs; onPrefs: (prefs: Prefs) => void }) {
  const options: { id: Theme; label: string; icon: ReactNode; hint: string }[] = [
    { id: "light", label: "Light", icon: <Sun size={16} />, hint: "Ink on near-white" },
    { id: "dark", label: "Dark", icon: <Moon size={16} />, hint: "AMOLED black" },
  ];
  return (
    <>
      <p className="muted small"><Monitor size={13} /> Saved in this browser.</p>
      <div className="theme-options" role="radiogroup" aria-label="Theme">
        {options.map((o) => (
          <button key={o.id} role="radio" aria-checked={theme === o.id} className={`theme-option ${o.id}${theme === o.id ? " active" : ""}`} onClick={() => onTheme(o.id)}>
            <span className="theme-swatch" aria-hidden><span /><span /><span /></span>
            <span className="theme-option-label">{o.icon} <strong>{o.label}</strong></span>
            <span className="muted small">{o.hint}</span>
          </button>
        ))}
      </div>
      <div className="pref-list">
        <PrefRow
          title="Open tool results automatically"
          hint="Show the results panel when a step runs or a chat with steps opens."
          checked={prefs.autoResults}
          onChange={(v) => onPrefs({ ...prefs, autoResults: v })}
        />
        <PrefRow
          title="Show model reasoning"
          hint="The model's thinking, for models that share it. Off hides it."
          checked={prefs.showReasoning}
          onChange={(v) => onPrefs({ ...prefs, showReasoning: v })}
        />
      </div>
    </>
  );
}

function PrefRow({ title, hint, checked, onChange }: { title: string; hint: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="pref-row">
      <span className="pref-text">
        <strong>{title}</strong>
        <span className="muted small">{hint}</span>
      </span>
      <SquishSwitch
        checked={checked}
        onChange={onChange}
        ariaLabel={title}
        width={44}
        height={24}
        trackColor="var(--bg-sunk)"
        trackOnColor="var(--primary)"
        thumbColor="var(--surface)"
        thumbOnColor="var(--primary-fg)"
      />
    </div>
  );
}

function ComingSoon({ intro, points, action }: { intro: string; points: string[]; action: string }) {
  return (
    <div className="coming-soon">
      <span className="soon-badge large">Coming soon</span>
      <p>{intro}</p>
      <SpotlightCard className="soon-card" theme={pageTheme()} shape="beam" intensity={0.12} proximity={0}>
        <ul>{points.map((p) => <li key={p}>{p}</li>)}</ul>
      </SpotlightCard>
      <div className="pane-actions">
        <button className="primary" disabled title="Coming soon">{action}</button>
      </div>
    </div>
  );
}
