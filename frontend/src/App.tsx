import { useCallback, useEffect, useMemo, useState } from "react";
import { api, send, type Chat, type Connection, type Message } from "./api";
import { ChatView, type ToolResult } from "./components/ChatView";
import { ConnectionsDialog } from "./components/ConnectionsDialog";
import { ResultsPanel } from "./components/ResultsPanel";
import { Sidebar } from "./components/Sidebar";

export default function App() {
  const [chats, setChats] = useState<Chat[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [notices, setNotices] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [showConnections, setShowConnections] = useState(false);
  const [selectedToolId, setSelectedToolId] = useState<string | null>(null);
  const [error, setError] = useState("");

  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));

  const refreshChats = useCallback(() => api.chats().then(setChats).catch(fail), []);
  const refreshConnections = useCallback(
    () => api.connections().then(setConnections).catch(fail),
    [],
  );

  useEffect(() => {
    refreshChats();
    refreshConnections();
  }, [refreshChats, refreshConnections]);

  const open = async (id: string) => {
    setActiveId(id);
    setNotices([]);
    setSelectedToolId(null);
    try {
      setMessages(await api.messages(id));
    } catch (e) {
      fail(e);
    }
  };

  const newChat = async () => {
    try {
      const chat = await api.newChat();
      await refreshChats();
      await open(chat.id);
    } catch (e) {
      fail(e);
    }
  };

  const deleteChat = async (id: string) => {
    try {
      await api.deleteChat(id);
      if (id === activeId) {
        setActiveId(null);
        setMessages([]);
      }
      await refreshChats();
    } catch (e) {
      fail(e);
    }
  };

  const sendMessage = async (text: string) => {
    setError("");
    let chatId = activeId;
    if (!chatId) {
      const chat = await api.newChat().catch((e) => (fail(e), null));
      if (!chat) return;
      chatId = chat.id;
      setActiveId(chat.id);
    }
    setMessages((m) => [...m, { role: "user", content: text }]);
    setNotices([]);
    setBusy(true);
    try {
      for await (const event of send(chatId, text)) {
        if (event.type === "message") {
          setMessages((m) => [...m, event.message]);
          const msg = event.message;
          if (msg.role === "assistant" && msg.tool_calls.length) {
            setSelectedToolId(msg.tool_calls[msg.tool_calls.length - 1].id);
          }
        } else if (event.type === "notice" || event.type === "error") {
          setNotices((n) => [...n, event.content]);
        }
      }
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
      refreshChats();
    }
  };

  const selected: ToolResult | null = useMemo(() => {
    if (!selectedToolId) return null;
    for (const m of messages) {
      if (m.role !== "assistant") continue;
      const call = m.tool_calls.find((c) => c.id === selectedToolId);
      if (call) {
        const result = messages.find(
          (r): r is Extract<Message, { role: "tool" }> =>
            r.role === "tool" && r.tool_call_id === call.id,
        );
        return { call, result };
      }
    }
    return null;
  }, [messages, selectedToolId]);

  return (
    <div className="layout">
      <Sidebar
        chats={chats}
        activeId={activeId}
        onSelect={open}
        onNew={newChat}
        onDelete={deleteChat}
        onConnections={() => setShowConnections(true)}
      />
      <main>
        {error && (
          <div className="error-bar" role="alert">
            {error} <button onClick={() => setError("")}>Dismiss</button>
          </div>
        )}
        <ChatView
          messages={messages}
          notices={notices}
          busy={busy}
          hasConnections={connections.length > 0}
          selectedToolId={selectedToolId}
          onSelectTool={setSelectedToolId}
          onSend={sendMessage}
        />
      </main>
      <ResultsPanel item={selected} />
      {showConnections && (
        <ConnectionsDialog
          connections={connections}
          onAdd={async (c) => {
            await api.addConnection(c);
            await refreshConnections();
          }}
          onDelete={async (id) => {
            await api.deleteConnection(id);
            await refreshConnections();
          }}
          onClose={() => setShowConnections(false)}
        />
      )}
    </div>
  );
}
