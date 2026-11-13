import { Cpu, Monitor, Moon, Palette, Plug, Sun, X } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import type { Connection, Level, ModelSelection, NewConnection } from "../api";
import { ConnectionsSettings } from "./ConnectionsSettings";
import { ModelSettings } from "./ModelSettings";

export type SettingsTab = "connections" | "model" | "appearance";
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
}

const TABS: { id: SettingsTab; label: string; icon: ReactNode; title: string }[] = [
  { id: "connections", label: "Connections", icon: <Plug size={15} />, title: "Connections" },
  { id: "model", label: "Model", icon: <Cpu size={15} />, title: "Bedrock model" },
  { id: "appearance", label: "Appearance", icon: <Palette size={15} />, title: "Appearance" },
];

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
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              className={`settings-tab${tab === t.id ? " active" : ""}`}
              onClick={() => onTab(t.id)}
            >
              {t.icon} {t.label}
            </button>
          ))}
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
          {tab === "appearance" && <Appearance theme={props.theme} onTheme={props.onTheme} />}
        </div>
      </div>
    </div>
  );
}

function Appearance({ theme, onTheme }: { theme: Theme; onTheme: (theme: Theme) => void }) {
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
    </>
  );
}
