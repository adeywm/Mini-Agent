import { Link2, MessageSquare, Pencil, Plug, Plus, Search, Trash2 } from "lucide-react";
import { useState } from "react";
import type { Chat, Connection } from "../api";
import { groupChats, LEVELS } from "../lib";
import { ProviderIcon } from "./Icons";

interface Props {
  chats: Chat[];
  connections: Connection[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onConnections: () => void;
}

export function Sidebar(props: Props) {
  const { chats, connections, activeId } = props;
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const filtered = chats.filter((c) => c.title.toLowerCase().includes(query.toLowerCase()));

  const commit = (id: string) => {
    const title = draft.trim();
    setEditing(null);
    if (title) props.onRename(id, title);
  };

  return (
    <aside className="sidebar">
      <div className="side-top">
        <div className="brand">
          <span className="logo">M</span> Mini-Agent
        </div>
        <button className="new-chat" onClick={props.onNew}>
          <Plus size={16} /> New chat
        </button>
        <label className="search">
          <Search size={14} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search chats"
            aria-label="Search chats"
          />
        </label>
      </div>

      <nav className="chat-groups" aria-label="Chats">
        {groupChats(filtered).map(([group, list]) => (
          <div key={group} className="chat-group">
            <div className="group-label">{group}</div>
            {list.map((c) => (
              <div key={c.id} className={`chat-item ${c.id === activeId ? "active" : ""}`}>
                {editing === c.id ? (
                  <input
                    className="rename"
                    autoFocus
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onBlur={() => commit(c.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") commit(c.id);
                      if (e.key === "Escape") setEditing(null);
                    }}
                    aria-label="Chat title"
                  />
                ) : (
                  <button className="chat-title" onClick={() => props.onSelect(c.id)} title={c.title}>
                    <MessageSquare size={14} />
                    <span>{c.title}</span>
                  </button>
                )}
                <div className="chat-actions">
                  <button
                    className="ghost icon-only"
                    aria-label={`Rename ${c.title}`}
                    onClick={() => {
                      setDraft(c.title);
                      setEditing(c.id);
                    }}
                  >
                    <Pencil size={13} />
                  </button>
                  <button
                    className="ghost icon-only"
                    aria-label={`Delete ${c.title}`}
                    onClick={() => props.onDelete(c.id)}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        ))}
        {filtered.length === 0 && (
          <p className="muted small pad">{query ? "No matching chats." : "No chats yet."}</p>
        )}
      </nav>

      <section className="conn-summary" aria-label="Connected accounts">
        <div className="conn-head">
          <span><Plug size={14} /> Connections</span>
          <button className="connect-shortcut" onClick={props.onConnections}><Plus size={14} /> Connect</button>
        </div>
        {connections.length === 0 ? (
          <span className="muted small">No accounts connected yet.</span>
        ) : (
          connections.map((c) => (
            <span key={c.id} className="conn-line">
              <ProviderIcon kind={c.kind} size={12} />
              <span className="conn-name">{c.name}</span>
              <span className={`lvl l${c.max_level}`}>{LEVELS[c.max_level].short}</span>
            </span>
          ))
        )}
        <button className="manage-connections" onClick={props.onConnections}><Link2 size={13} /> Manage accounts</button>
      </section>
    </aside>
  );
}
