import { PanelLeft, PanelRight } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, send, type AssistantMessage, type Chat, type Connection, type Message } from "./api";
import { ChatView } from "./components/ChatView";
import { ConnectionsDialog } from "./components/ConnectionsDialog";
import { ProviderIcon } from "./components/Icons";
import { ResultsPanel } from "./components/ResultsPanel";
import { Sidebar } from "./components/Sidebar";
import { Toasts, type Toast } from "./components/Toasts";
import { allSteps, LEVELS } from "./lib";

const wide = () => window.matchMedia("(min-width: 1200px)").matches;

export default function App() {
  const [chats, setChats] = useState<Chat[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [timings, setTimings] = useState<Record<string, number>>({});
  const [notices, setNotices] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [selectedToolId, setSelectedToolId] = useState<string | null>(null);
  const [showResults, setShowResults] = useState(false);
  const [showSidebar, setShowSidebar] = useState(true);
  const [showConnections, setShowConnections] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const abort = useRef<AbortController | null>(null);
  const started = useRef<Record<string, number>>({});

  const toast = useCallback((e: unknown) => {
    const text = e instanceof Error ? e.message : String(e);
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 6000);
  }, []);

  const refreshChats = useCallback(() => api.chats().then(setChats).catch(toast), [toast]);
  const refreshConnections = useCallback(() => api.connections().then(setConnections).catch(toast), [toast]);

  useEffect(() => {
    refreshChats();
    refreshConnections();
    const fromUrl = window.location.hash.slice(1);
    if (fromUrl.startsWith("chat-")) open(fromUrl);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshChats, refreshConnections]);

  useEffect(() => {
    const hash = activeId ? `#${activeId}` : " ";
    if (window.location.hash !== hash.trim()) history.replaceState(null, "", hash);
  }, [activeId]);

  const steps = useMemo(() => allSteps(messages, timings), [messages, timings]);
  const activeChat = chats.find((c) => c.id === activeId);

  const open = async (id: string) => {
    if (busy) return;
    setActiveId(id);
    setNotices([]);
    setSelectedToolId(null);
    setTimings({});
    try {
      setMessages(await api.messages(id));
    } catch (e) {
      toast(e);
    }
  };

  const newChat = () => {
    if (busy) return;
    setActiveId(null);
    setMessages([]);
    setNotices([]);
    setSelectedToolId(null);
    setShowResults(false);
  };

  const deleteChat = async (id: string) => {
    try {
      await api.deleteChat(id);
      if (id === activeId) newChat();
      await refreshChats();
    } catch (e) {
      toast(e);
    }
  };

  const renameChat = async (id: string, title: string) => {
    try {
      await api.renameChat(id, title);
      await refreshChats();
    } catch (e) {
      toast(e);
    }
  };

  const applyEvent = (event: Awaited<ReturnType<ReturnType<typeof send>["next"]>>["value"]) => {
    if (!event) return;
    if (event.type === "token") {
      setMessages((list) => {
        const i = list.findIndex((m) => m.role === "assistant" && m.id === event.id);
        if (i < 0) {
          return [...list, { role: "assistant", id: event.id, content: event.text, tool_calls: [], streaming: true }];
        }
        const m = list[i] as AssistantMessage;
        const next = [...list];
        next[i] = { ...m, content: m.content + event.text };
        return next;
      });
    } else if (event.type === "message") {
      const msg = event.message;
      if (msg.role === "assistant") {
        const now = performance.now();
        for (const c of msg.tool_calls) started.current[c.id] = now;
        if (msg.tool_calls.length) {
          setSelectedToolId(msg.tool_calls[msg.tool_calls.length - 1].id);
          if (wide()) setShowResults(true);
        }
        setMessages((list) => {
          const i = list.findIndex((m) => m.role === "assistant" && m.id === msg.id);
          if (i < 0) return [...list, msg];
          const next = [...list];
          next[i] = msg;
          return next;
        });
      } else {
        if (msg.role === "tool" && started.current[msg.tool_call_id] !== undefined) {
          const ms = Math.round(performance.now() - started.current[msg.tool_call_id]);
          setTimings((t) => ({ ...t, [msg.tool_call_id]: ms }));
        }
        setMessages((list) => [...list, msg]);
      }
    } else if (event.type === "notice" || event.type === "error") {
      setNotices((n) => [...n, event.content]);
    }
  };

  const sendMessage = async (text: string) => {
    let chatId = activeId;
    if (!chatId) {
      try {
        const chat = await api.newChat();
        chatId = chat.id;
        setActiveId(chat.id);
      } catch (e) {
        toast(e);
        return;
      }
    }
    setMessages((m) => [...m, { role: "user", content: text }]);
    setNotices([]);
    setBusy(true);
    const controller = new AbortController();
    abort.current = controller;
    try {
      for await (const event of send(chatId, text, controller.signal)) applyEvent(event);
    } catch (e) {
      if (controller.signal.aborted) setNotices((n) => [...n, "Stopped."]);
      else toast(e);
    } finally {
      setMessages((list) => list.map((m) => (m.role === "assistant" && m.streaming ? { ...m, streaming: false } : m)));
      setBusy(false);
      abort.current = null;
      refreshChats();
    }
  };

  return (
    <div className={`app ${showSidebar ? "" : "no-sidebar"} ${showResults ? "with-results" : ""}`}>
      {showSidebar && (
        <Sidebar
          chats={chats}
          connections={connections}
          activeId={activeId}
          onSelect={open}
          onNew={newChat}
          onRename={renameChat}
          onDelete={deleteChat}
          onConnections={() => setShowConnections(true)}
        />
      )}
      <main className="main">
        <header className="topbar">
          <button className="ghost icon-only" aria-label="Toggle sidebar" onClick={() => setShowSidebar(!showSidebar)}>
            <PanelLeft size={18} />
          </button>
          <h1 className="chat-name">{activeChat?.title ?? "New chat"}</h1>
          <div className="top-conns">
            {connections.map((c) => (
              <span key={c.id} className="pill" title={`${c.kind} · ${LEVELS[c.max_level].label}`}>
                <ProviderIcon kind={c.kind} size={12} /> {c.name}
                <span className={`lvl l${c.max_level}`}>{LEVELS[c.max_level].short}</span>
              </span>
            ))}
          </div>
          <button className={`ghost results-toggle ${showResults ? "on" : ""}`} onClick={() => setShowResults(!showResults)} aria-label="Toggle results">
            <PanelRight size={18} />
            {steps.length > 0 && <span className="count">{steps.length}</span>}
          </button>
        </header>
        <ChatView
          chatId={activeId}
          messages={messages}
          timings={timings}
          notices={notices}
          busy={busy}
          connections={connections}
          selectedToolId={selectedToolId}
          onOpenTool={(id) => {
            setSelectedToolId(id);
            setShowResults(true);
          }}
          onSend={sendMessage}
          onStop={() => abort.current?.abort()}
          onConnections={() => setShowConnections(true)}
        />
      </main>
      {showResults && (
        <ResultsPanel
          steps={steps}
          selectedId={selectedToolId}
          connections={connections}
          onSelect={setSelectedToolId}
          onClose={() => setShowResults(false)}
        />
      )}
      {showConnections && (
        <ConnectionsDialog
          connections={connections}
          onAdd={async (c) => {
            await api.addConnection(c);
            await refreshConnections();
          }}
          onDelete={async (id) => {
            try {
              await api.deleteConnection(id);
              await refreshConnections();
            } catch (e) {
              toast(e);
            }
          }}
          onClose={() => setShowConnections(false)}
        />
      )}
      <Toasts toasts={toasts} onDismiss={(id) => setToasts((t) => t.filter((x) => x.id !== id))} />
    </div>
  );
}
