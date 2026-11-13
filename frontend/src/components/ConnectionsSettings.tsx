import { AlertCircle, Check, ExternalLink, Loader2, LogIn, Plus, RefreshCw, Unplug } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, type Connection, type ConnectionStatus, type Level, type LocalAccounts, type LoginStart, type NewConnection } from "../api";
import { LEVELS } from "../lib";
import { ProviderIcon } from "./Icons";
import { CopyButton } from "./Markdown";

interface Props {
  connections: Connection[];
  onAdd: (c: NewConnection) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onSetLevel: (id: string, level: Level) => Promise<void>;
}

const LEVEL_HELP: Record<Level, string> = {
  1: "Reads only. A change asks you to elevate for that request.",
  2: "Creates and updates. Each one still asks for approval.",
  3: "Deletes and IAM too. Each one still asks for approval.",
};

const PROVIDERS = {
  github: { title: "GitHub", hint: "Uses your gh CLI login" },
  aws: { title: "AWS", hint: "Uses a profile from your AWS config" },
} as const;

/** Fixes the server can run for the user (the rest need a terminal). */
const SIGN_IN = /^(gh auth login|aws sso login|aws login)\b/;
const LOGIN_POLL_MS = 3000;
const LOGIN_TIMEOUT_MS = 5 * 60 * 1000;

const REGIONS = ["ap-south-1", "ap-south-2", "us-east-1", "us-east-2", "us-west-2", "eu-west-1", "eu-central-1", "ap-southeast-1"];

/** Settings > Connections: accounts with live sign-in status, and adding a new one. */
export function ConnectionsSettings({ connections, onAdd, onDelete, onSetLevel }: Props) {
  const [kind, setKind] = useState<"aws" | "github">("github");
  const [name, setName] = useState("");
  const [githubUser, setGithubUser] = useState("");
  const [profile, setProfile] = useState("");
  const [region, setRegion] = useState("ap-south-1");
  const [level, setLevel] = useState<Level>(1);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<Record<string, ConnectionStatus>>({});
  const [checking, setChecking] = useState(false);
  const [local, setLocal] = useState<LocalAccounts | null>(null);

  const checkAll = useCallback(async () => {
    setChecking(true);
    try {
      const list = await api.connectionStatus();
      setStatus(Object.fromEntries(list.map((s) => [s.id, s])));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    void checkAll();
    api.localAccounts().then(setLocal, () => setLocal(null));
  }, [checkAll]);

  // Sign-ins in progress, by connection id; polled until the connection reports signed in.
  const [logins, setLogins] = useState<Record<string, LoginStart & { since: number }>>({});
  const signIn = async (id: string) => {
    setError("");
    try {
      const started = await api.login(id);
      setLogins((l) => ({ ...l, [id]: { ...started, since: Date.now() } }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  const waiting = Object.keys(logins).length > 0;
  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(() => void checkAll(), LOGIN_POLL_MS);
    return () => clearInterval(timer);
  }, [waiting, checkAll]);
  useEffect(() => {
    setLogins((l) => {
      const keep = Object.entries(l).filter(([id, s]) =>
        status[id]?.state !== "ok" && Date.now() - s.since < LOGIN_TIMEOUT_MS);
      return keep.length === Object.keys(l).length ? l : Object.fromEntries(keep);
    });
  }, [status]);

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
      void checkAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const ready = name.trim() && (kind === "aws" ? region.trim() : githubUser.trim());

  return (
    <>
        <p className="muted small">
          Sign-in stays with <code>gh</code> and your AWS config. This app stores names, never credentials.
        </p>

        <section className="settings-section">
          <div className="section-head">
            <span className="group-label">Accounts · {connections.length}</span>
            {connections.length > 0 && (
              <button className="ghost xs" onClick={checkAll} disabled={checking}>
                <RefreshCw size={13} className={checking ? "spin" : undefined} /> Check again
              </button>
            )}
          </div>
          {connections.length === 0 ? (
            <div className="conn-empty">No accounts yet. Add GitHub or AWS below.</div>
          ) : (
            <ul className="conn-list">
              {connections.map((c) => (
                <ConnectionRow
                  key={c.id}
                  connection={c}
                  status={status[c.id]}
                  checking={checking}
                  disabled={saving}
                  login={logins[c.id]}
                  onSignIn={() => signIn(c.id)}
                  onSetLevel={async (l) => {
                    setError("");
                    setSaving(true);
                    try { await onSetLevel(c.id, l); }
                    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
                    finally { setSaving(false); }
                  }}
                  onDelete={() => onDelete(c.id)}
                />
              ))}
            </ul>
          )}
        </section>

        <section className="settings-section">
          <div className="section-head">
            <span className="group-label">Add an account</span>
          </div>
          <div className="provider-tiles" role="radiogroup" aria-label="Account type">
            {(["github", "aws"] as const).map((k) => (
              <button key={k} role="radio" aria-checked={kind === k} className={`provider-tile${kind === k ? " active" : ""}`} onClick={() => setKind(k)}>
                <span className="conn-icon"><ProviderIcon kind={k} size={16} /></span>
                <span>
                  <strong>{PROVIDERS[k].title}</strong>
                  <span className="muted small">{PROVIDERS[k].hint}</span>
                </span>
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
                GitHub account
                <input value={githubUser} onChange={(e) => setGithubUser(e.target.value)} placeholder="octocat" list="gh-users" />
                <datalist id="gh-users">{local?.github_users.map((u) => <option key={u} value={u} />)}</datalist>
              </label>
            ) : (
              <>
                <label>
                  AWS profile
                  <input value={profile} onChange={(e) => setProfile(e.target.value)} placeholder="default" list="aws-profiles" />
                  <datalist id="aws-profiles">{local?.aws_profiles.map((p) => <option key={p} value={p} />)}</datalist>
                </label>
                <label>
                  Region
                  <input value={region} onChange={(e) => setRegion(e.target.value)} list="aws-regions" />
                  <datalist id="aws-regions">{REGIONS.map((r) => <option key={r} value={r} />)}</datalist>
                </label>
              </>
            )}
          </div>
          <LocalHint kind={kind} local={local} />

          <div className="level-options" role="radiogroup" aria-label="Default level">
            {([1, 2, 3] as Level[]).map((l) => (
              <button key={l} role="radio" aria-checked={level === l} className={`level-option${level === l ? " active" : ""}`} onClick={() => setLevel(l)}>
                <span className="level-option-head">
                  <span className={`lvl l${l}`}>{LEVELS[l].short}</span>
                  <strong>{LEVELS[l].label}</strong>
                </span>
                <span className="muted small">{LEVEL_HELP[l]}</span>
              </button>
            ))}
          </div>
        </section>

        {error && <p className="error-text">{error}</p>}
        <div className="pane-actions">
          <button className="primary" onClick={submit} disabled={!ready || saving}>
            {saving ? <Loader2 size={14} className="spin" /> : <Plus size={14} />} Connect {PROVIDERS[kind].title}
          </button>
        </div>
    </>
  );
}

function ConnectionRow({ connection: c, status, checking, disabled, login, onSignIn, onSetLevel, onDelete }: {
  connection: Connection;
  status?: ConnectionStatus;
  checking: boolean;
  disabled: boolean;
  login?: LoginStart;
  onSignIn: () => Promise<void>;
  onSetLevel: (level: Level) => void;
  onDelete: () => void;
}) {
  const meta = c.kind === "github"
    ? c.github_user
    : [c.aws_profile ?? "default", c.aws_region, status?.identity].filter(Boolean).join(" · ");
  return (
    <li className="conn-row">
      <div className="conn-main">
        <span className="conn-icon"><ProviderIcon kind={c.kind} size={16} /></span>
        <div className="conn-info">
          <span className="conn-title">
            <strong>{c.name}</strong>
            <StatusBadge status={status} checking={checking} />
          </span>
          <span className="conn-meta">{meta}</span>
        </div>
        <select
          className="level-select"
          value={c.max_level}
          disabled={disabled}
          onChange={(e) => onSetLevel(Number(e.target.value) as Level)}
          aria-label={`Default level for ${c.name}`}
        >
          {([1, 2, 3] as Level[]).map((l) => <option key={l} value={l}>{LEVELS[l].short} · {LEVELS[l].label}</option>)}
        </select>
        <button className="ghost icon-only danger-ghost" title={`Disconnect ${c.name}`} aria-label={`Disconnect ${c.name}`} onClick={onDelete}>
          <Unplug size={15} />
        </button>
      </div>
      {status && status.state !== "ok" && (login ? (
        <div className="conn-fix signing-in" role="status">
          <Loader2 size={14} className="spin" aria-hidden />
          <span>
            {login.code ? "Enter this code on GitHub, then approve." : "Finish signing in in your browser."} Waiting…
          </span>
          {login.code && (
            <span className="conn-fix-command">
              <code className="login-code">{login.code}</code>
              <CopyButton text={login.code} label="Copy" />
            </span>
          )}
          {login.url && (
            <button className="xs" onClick={() => window.open(login.url!, "_blank", "noopener")}>
              <ExternalLink size={13} /> Open page
            </button>
          )}
        </div>
      ) : (
        <div className={`conn-fix ${status.state}`} role="status">
          <AlertCircle size={14} aria-hidden />
          <span>{status.detail}</span>
          {status.fix && SIGN_IN.test(status.fix) ? (
            <SignInButton onSignIn={onSignIn} />
          ) : status.fix && (
            <span className="conn-fix-command">
              <code>{status.fix}</code>
              <CopyButton text={status.fix} label="Copy" />
            </span>
          )}
        </div>
      ))}
    </li>
  );
}

function SignInButton({ onSignIn }: { onSignIn: () => Promise<void> }) {
  const [starting, setStarting] = useState(false);
  return (
    <button className="primary xs" disabled={starting} onClick={async () => {
      setStarting(true);
      try { await onSignIn(); } finally { setStarting(false); }
    }}>
      {starting ? <Loader2 size={13} className="spin" /> : <LogIn size={13} />} Sign in
    </button>
  );
}

function StatusBadge({ status, checking }: { status?: ConnectionStatus; checking: boolean }) {
  if (!status) return <span className="status-badge pending">{checking ? "Checking…" : "Unknown"}</span>;
  if (status.state === "ok") return <span className="status-badge ok"><Check size={11} /> Signed in</span>;
  if (status.state === "signed_out") return <span className="status-badge warn">Sign-in needed</span>;
  return <span className="status-badge bad">Error</span>;
}

/** What this machine has signed in, so the user knows what to type or what to run first. */
function LocalHint({ kind, local }: { kind: "aws" | "github"; local: LocalAccounts | null }) {
  if (!local) return null;
  if (kind === "github") {
    if (!local.gh_installed) {
      return <p className="local-hint">GitHub CLI is not installed. Install it from cli.github.com, then run <code>gh auth login</code>.</p>;
    }
    if (!local.github_users.length) {
      return <p className="local-hint">No GitHub CLI login found. Run <code>gh auth login</code> in a terminal first.</p>;
    }
    return <p className="local-hint ok">Signed in with gh: {local.github_users.join(", ")}</p>;
  }
  if (!local.aws_profiles.length) {
    return <p className="local-hint">No AWS profiles found. Run <code>aws configure sso</code> in a terminal first.</p>;
  }
  return <p className="local-hint ok">Profiles on this machine: {local.aws_profiles.join(", ")}</p>;
}
