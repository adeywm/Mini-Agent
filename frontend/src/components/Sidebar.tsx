import { MessageSquare, Pencil, Plus, Search, Settings, Trash2 } from "lucide-react";
import { useState } from "react";
import type { Chat } from "../api";
import { groupChats } from "../lib";

interface Props {
  chats: Chat[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onSettings: () => void;
}

export function Sidebar(props: Props) {
  const { chats, activeId } = props;
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

      <button className="settings-button" onClick={props.onSettings}>
        <Settings size={16} /> Settings
      </button>
    </aside>
  );
}
