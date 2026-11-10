import { Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { Connection, Level, NewConnection } from "../api";
import { LEVELS } from "../lib";
import { ProviderIcon } from "./Icons";

interface Props {
  connections: Connection[];
  onAdd: (c: NewConnection) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onClose: () => void;
}

const LEVEL_HELP: Record<Level, string> = {
  1: "Reads only. Nothing can be changed.",
  2: "Can propose creates and updates; each one asks you first.",
  3: "Can also propose deletes; each one asks you first.",
};

export function ConnectionsDialog({ connections, onAdd, onDelete, onClose }: Props) {
  const [kind, setKind] = useState<"aws" | "github">("github");
  const [name, setName] = useState("");
  const [githubUser, setGithubUser] = useState("");
  const [profile, setProfile] = useState("");
  const [region, setRegion] = useState("ap-south-1");
  const [level, setLevel] = useState<Level>(1);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const submit = async () => {
    setError("");
    setSaving(true);
    try {
      await onAdd({
        kind,
        name: name.trim(),
        max_level: level,
        github_user: kind === "github" ? githubUser.trim() : null,
        aws_profile: kind === "aws" ? profile.trim() || null : null,
        aws_region: kind === "aws" ? region.trim() : null,
      });
      setName("");
      setGithubUser("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="conn-title">
        <header>
          <h2 id="conn-title">Connections</h2>
          <button className="ghost icon-only" aria-label="Close" onClick={onClose}>
            <X size={18} />
          </button>
        </header>
        <p className="muted small">
          No secrets are stored. GitHub uses your <code>gh auth login</code> account; AWS uses a profile
          from your AWS config.
        </p>

        <div className="conn-cards">
          {connections.map((c) => (
            <div key={c.id} className="conn-card">
              <div className="conn-icon">
                <ProviderIcon kind={c.kind} size={18} />
              </div>
              <div className="conn-info">
                <strong>{c.name}</strong>
                <span className="muted small">
                  {c.kind === "github" ? `GitHub · ${c.github_user}` : `AWS · ${c.aws_profile ?? "default"} · ${c.aws_region}`}
                </span>
              </div>
              <span className={`lvl l${c.max_level}`} title={LEVEL_HELP[c.max_level]}>
                {LEVELS[c.max_level].short} · {LEVELS[c.max_level].label}
              </span>
              <button className="ghost icon-only" aria-label={`Remove ${c.name}`} onClick={() => onDelete(c.id)}>
                <Trash2 size={15} />
              </button>
            </div>
          ))}
          {connections.length === 0 && <p className="muted small">No connections yet.</p>}
        </div>

        <h3>Add a connection</h3>
        <div className="segmented" role="radiogroup" aria-label="Type">
          {(["github", "aws"] as const).map((k) => (
            <button key={k} role="radio" aria-checked={kind === k} className={kind === k ? "active" : ""} onClick={() => setKind(k)}>
              <ProviderIcon kind={k} /> {k === "github" ? "GitHub" : "AWS"}
            </button>
          ))}
        </div>
        <div className="form-grid">
          <label>
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder={kind === "github" ? "personal" : "work-aws"} />
          </label>
          {kind === "github" ? (
            <label>
              GitHub user (gh login)
              <input value={githubUser} onChange={(e) => setGithubUser(e.target.value)} placeholder="octocat" />
            </label>
          ) : (
            <>
              <label>
                AWS profile
                <input value={profile} onChange={(e) => setProfile(e.target.value)} placeholder="default" />
              </label>
              <label>
                Region
                <input value={region} onChange={(e) => setRegion(e.target.value)} />
              </label>
            </>
          )}
        </div>
        <div className="level-pick">
          <span className="small">Maximum permission</span>
          <div className="segmented" role="radiogroup" aria-label="Maximum permission">
            {([1, 2, 3] as Level[]).map((l) => (
              <button key={l} role="radio" aria-checked={level === l} className={level === l ? "active" : ""} onClick={() => setLevel(l)}>
                {LEVELS[l].short} · {LEVELS[l].label}
              </button>
            ))}
          </div>
          <span className="muted small">{LEVEL_HELP[level]} This version runs reads only.</span>
        </div>
        {error && <p className="error-text">{error}</p>}
        <div className="dialog-actions">
          <button onClick={onClose}>Done</button>
          <button className="primary" onClick={submit} disabled={!name.trim() || saving}>
            Add connection
          </button>
        </div>
      </div>
    </div>
  );
}
