import { useState } from "react";
import type { Connection, Level, NewConnection } from "../api";

const LEVELS: Record<Level, string> = {
  1: "L1 · read only",
  2: "L2 · create / update (asks you)",
  3: "L3 · delete (always asks you)",
};

interface Props {
  connections: Connection[];
  onAdd: (c: NewConnection) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onClose: () => void;
}

export function ConnectionsDialog({ connections, onAdd, onDelete, onClose }: Props) {
  const [kind, setKind] = useState<"aws" | "github">("github");
  const [name, setName] = useState("");
  const [githubUser, setGithubUser] = useState("");
  const [profile, setProfile] = useState("");
  const [region, setRegion] = useState("ap-south-1");
  const [level, setLevel] = useState<Level>(1);
  const [error, setError] = useState("");

  const submit = async () => {
    setError("");
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
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label="Connections">
      <div className="dialog">
        <header>
          <h2>Connections</h2>
          <button className="icon" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>
        <p className="muted small">
          Credentials are never stored here: GitHub uses your <code>gh auth login</code> account,
          AWS uses a profile from your AWS config.
        </p>
        <ul className="conn-list">
          {connections.map((c) => (
            <li key={c.id}>
              <strong>{c.name}</strong> <span className="badge">{c.kind}</span>{" "}
              <span className="muted small">
                {c.kind === "github"
                  ? c.github_user
                  : `${c.aws_profile ?? "default"} · ${c.aws_region}`}{" "}
                · {LEVELS[c.max_level]}
              </span>
              <button className="icon" aria-label={`Remove ${c.name}`} onClick={() => onDelete(c.id)}>
                ×
              </button>
            </li>
          ))}
          {connections.length === 0 && <li className="muted">No connections yet.</li>}
        </ul>
        <h3>Add connection</h3>
        <div className="form">
          <label>
            Type
            <select value={kind} onChange={(e) => setKind(e.target.value as "aws" | "github")}>
              <option value="github">GitHub</option>
              <option value="aws">AWS</option>
            </select>
          </label>
          <label>
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="personal" />
          </label>
          {kind === "github" ? (
            <label>
              GitHub user (gh login)
              <input value={githubUser} onChange={(e) => setGithubUser(e.target.value)} />
            </label>
          ) : (
            <>
              <label>
                AWS profile
                <input
                  value={profile}
                  onChange={(e) => setProfile(e.target.value)}
                  placeholder="default"
                />
              </label>
              <label>
                Region
                <input value={region} onChange={(e) => setRegion(e.target.value)} />
              </label>
            </>
          )}
          <label>
            Maximum level
            <select value={level} onChange={(e) => setLevel(Number(e.target.value) as Level)}>
              {([1, 2, 3] as Level[]).map((l) => (
                <option key={l} value={l}>
                  {LEVELS[l]}
                </option>
              ))}
            </select>
          </label>
          {error && <p className="warn">{error}</p>}
          <button className="primary" onClick={submit} disabled={!name.trim()}>
            Add
          </button>
          <p className="muted small">This version runs reads only; changes arrive next.</p>
        </div>
      </div>
    </div>
  );
}
