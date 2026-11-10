import type { Chat } from "../api";

interface Props {
  chats: Chat[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onConnections: () => void;
}

export function Sidebar({ chats, activeId, onSelect, onNew, onDelete, onConnections }: Props) {
  return (
    <aside className="sidebar">
      <div className="brand">Mini-Agent</div>
      <button className="primary" onClick={onNew}>
        + New chat
      </button>
      <nav className="chat-list" aria-label="Chats">
        {chats.map((c) => (
          <div key={c.id} className={`chat-item ${c.id === activeId ? "active" : ""}`}>
            <button className="chat-title" onClick={() => onSelect(c.id)} title={c.title}>
              {c.title}
            </button>
            <button className="icon" aria-label={`Delete ${c.title}`} onClick={() => onDelete(c.id)}>
              ×
            </button>
          </div>
        ))}
        {chats.length === 0 && <p className="muted small">No chats yet.</p>}
      </nav>
      <button onClick={onConnections}>Connections</button>
    </aside>
  );
}
