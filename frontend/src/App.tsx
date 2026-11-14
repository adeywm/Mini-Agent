import { PanelLeft, PanelRight } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, decide, send, type Approval, type AssistantMessage, type Chat, type ChatNotice, type Connection, type Message, type ModelSelection, type StreamEvent } from "./api";
import { ChatView } from "./components/ChatView";
import { ResultsPanel } from "./components/ResultsPanel";
import { SettingsDialog, type SettingsTab, type Theme } from "./components/SettingsDialog";
import { Sidebar } from "./components/Sidebar";
import { Toasts, type Toast } from "./components/Toasts";
import { addAuthNotice, allSteps, loadPrefs, markPendingUnknown, savePrefs, type Prefs } from "./lib";

const wide = () => window.matchMedia("(min-width: 1200px)").matches;

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem("mini-agent-theme");
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    // Storage can be unavailable in private browsing.
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export default function App() {
  const [chats, setChats] = useState<Chat[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [timings, setTimings] = useState<Record<string, number>>({});
  const [notices, setNotices] = useState<ChatNotice[]>([]);
  const [busy, setBusy] = useState(false);
  const [selectedToolId, setSelectedToolId] = useState<string | null>(null);
  const [showResults, setShowResults] = useState(false);
  const [showSidebar, setShowSidebar] = useState(true);
  const [settings, setSettings] = useState<SettingsTab | null>(null);
  const [model, setModel] = useState<ModelSelection | null>(null);
  const [modelError, setModelError] = useState<string | null>(null);
  const [loadingModel, setLoadingModel] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [prefs, setPrefsState] = useState<Prefs>(loadPrefs);
  const prefsRef = useRef(prefs);
  prefsRef.current = prefs;
  const setPrefs = (next: Prefs) => {
    setPrefsState(next);
    savePrefs(next);
  };
  const abort = useRef<AbortController | null>(null);
  const started = useRef<Record<string, number>>({});
  const thinkingSince = useRef<Record<string, number>>({});

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("mini-agent-theme", theme);
    } catch {
      // Keep the current theme even when persistence is unavailable.
    }
  }, [theme]);

  const toast = useCallback((e: unknown) => {
    const text = e instanceof Error ? e.message : String(e);
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 6000);
  }, []);

  const refreshChats = useCallback(() => api.chats().then(setChats).catch(toast), [toast]);
  const refreshConnections = useCallback(() => api.connections().then(setConnections).catch(toast), [toast]);
  const refreshModel = useCallback(async () => {
    setLoadingModel(true);
    setModelError(null);
    try {
      setModel(await api.model());
    } catch (e) {
      setModelError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingModel(false);
    }
  }, []);

  useEffect(() => {
    refreshChats();
    refreshConnections();
    refreshModel();
    const fromUrl = window.location.hash.slice(1);
    if (fromUrl.startsWith("chat-")) open(fromUrl);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshChats, refreshConnections, refreshModel]);

  useEffect(() => {
    const hash = activeId ? `#${activeId}` : " ";
    if (window.location.hash !== hash.trim()) history.replaceState(null, "", hash);
  }, [activeId]);

  const steps = useMemo(() => {
    const awaiting = new Set(approvals.map((a) => a.tool_call_id));
    return allSteps(messages, timings).map((s) => (awaiting.has(s.call.id) && !s.result ? { ...s, awaiting: true } : s));
  }, [messages, timings, approvals]);
  const activeChat = chats.find((c) => c.id === activeId);
  // Every change waits for an approval card, which elevates a connection when needed.
  const changesEnabled = connections.length > 0;

  const open = async (id: string) => {
    if (busy) return;
    setActiveId(id);
    setNotices([]);
    setSelectedToolId(null);
    setShowResults(false);
    setTimings({});
    setApprovals([]);
    try {
      const [loaded, waiting] = await Promise.all([api.messages(id), api.approvals(id)]);
      setMessages(loaded);
      setApprovals(waiting);
      const savedSteps = allSteps(loaded, {});
      const latest = savedSteps[savedSteps.length - 1];
      if (latest) {
        setSelectedToolId(latest.call.id);
        if (prefsRef.current.autoResults) setShowResults(true);
      }
    } catch (e) {
      toast(e);
    }
  };

  const newChat = () => {
    if (busy) return;
    setActiveId(null);
    setMessages([]);
    setApprovals([]);
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

  const applyEvent = (event: StreamEvent) => {
    if (event.type === "token" || event.type === "reasoning") {
      const now = performance.now();
      if (event.type === "reasoning") thinkingSince.current[event.id] ??= now;
      setMessages((list) => {
        const i = list.findIndex((m) => m.role === "assistant" && m.id === event.id);
        const m: AssistantMessage = i < 0
          ? { role: "assistant", id: event.id, content: "", reasoning: "", tool_calls: [], streaming: true }
          : (list[i] as AssistantMessage);
        let updated: AssistantMessage;
        if (event.type === "reasoning") {
          updated = { ...m, reasoning: (m.reasoning ?? "") + event.text };
        } else {
          const since = thinkingSince.current[event.id];
          const reasoningMs = m.reasoningMs ?? (since !== undefined ? Math.round(now - since) : undefined);
          updated = { ...m, content: m.content + event.text, reasoningMs };
        }
        if (i < 0) return [...list, updated];
        const next = [...list];
        next[i] = updated;
        return next;
      });
    } else if (event.type === "message") {
      const msg = event.message;
      if (msg.role === "assistant") {
        const now = performance.now();
        for (const c of msg.tool_calls) started.current[c.id] = now;
        if (msg.tool_calls.length) {
          setSelectedToolId(msg.tool_calls[msg.tool_calls.length - 1].id);
          if (wide() && prefsRef.current.autoResults) setShowResults(true);
        }
        setMessages((list) => {
          const i = list.findIndex((m) => m.role === "assistant" && m.id === msg.id);
          if (i < 0) return [...list, msg];
          const next = [...list];
          const before = next[i] as AssistantMessage;
          const since = thinkingSince.current[msg.id];
          next[i] = {
            ...msg,
            reasoningMs: before.reasoningMs ?? (since !== undefined ? Math.round(performance.now() - since) : undefined),
          };
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
      setNotices((n) => [...n, { type: event.type, content: event.content }]);
    } else if (event.type === "auth_required") {
      setNotices((notices) => addAuthNotice(notices, event));
    } else if (event.type === "approval") {
      const card = event.approval;
      setApprovals((list) => [...list.filter((a) => a.id !== card.id), card]);
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
    setApprovals([]); // a new message dismisses changes still waiting (the server says "Not run")
    await stream(chatId, (signal) => send(chatId as string, text, signal));
  };

  /** Approve or reject one change card, then follow the resumed run. */
  const decideApproval = async (approval: Approval, decision: "approve" | "reject", message?: string) => {
    if (!activeId || busy) return;
    const chatId = activeId;
    setApprovals((list) => list.filter((a) => a.id !== approval.id));
    // Time the step from the decision, not from when the card appeared.
    started.current[approval.tool_call_id] = performance.now();
    await stream(chatId, (signal) =>
      decide(chatId, approval.id, { decision, digest: approval.digest, message }, signal));
  };

  const stream = async (chatId: string, start: (signal: AbortSignal) => AsyncGenerator<StreamEvent>) => {
    setNotices([]);
    setBusy(true);
    const controller = new AbortController();
    abort.current = controller;
    const waiting = new Set<string>();
    try {
      for await (const event of start(controller.signal)) {
        if (event.type === "approval") waiting.add(event.approval.tool_call_id);
        applyEvent(event);
      }
    } catch (e) {
      if (controller.signal.aborted) {
        setNotices((n) => [...n, { type: "notice", content: "Response stopped. A running tool may still finish; its outcome is unknown." }]);
      }
      else toast(e);
    } finally {
      let still = waiting;
      try {
        const current = await api.approvals(chatId);
        still = new Set(current.map((a) => a.tool_call_id));
        setApprovals(current);
      } catch {
        // Keep the cards from the stream if the list cannot be reloaded.
      }
      setMessages((list) => markPendingUnknown(
        list.map((m) => (m.role === "assistant" && m.streaming ? { ...m, streaming: false } : m)),
        controller.signal.aborted,
        still,
      ));
      setBusy(false);
      abort.current = null;
      refreshChats();
      // "Notify me when done": only when the user is looking elsewhere.
      if (prefsRef.current.notify && document.hidden && "Notification" in window && Notification.permission === "granted") {
        new Notification("Mini-Agent", {
          body: controller.signal.aborted ? "The response was stopped."
            : still.size ? "A change is waiting for your approval."
            : "Your answer is ready.",
        });
      }
    }
  };

  return (
    <div className={`app ${showSidebar ? "" : "no-sidebar"} ${showResults ? "with-results" : ""}`}>
      {showSidebar && (
        <Sidebar
          chats={chats}
          activeId={activeId}
          onSelect={open}
          onNew={newChat}
          onRename={renameChat}
          onDelete={deleteChat}
          onSettings={() => setSettings("connections")}
        />
      )}
      <main className="main">
        <header className="topbar">
          <button className="ghost icon-only" aria-label="Toggle sidebar" onClick={() => setShowSidebar(!showSidebar)}>
            <PanelLeft size={18} />
          </button>
          <h1 className="chat-name">{activeChat?.title ?? "New chat"}</h1>
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
          changesEnabled={changesEnabled}
          approvals={approvals}
          onDecide={decideApproval}
          selectedToolId={selectedToolId}
          onOpenTool={(id) => {
            setSelectedToolId(id);
            setShowResults(true);
          }}
          onSend={sendMessage}
          showReasoning={prefs.showReasoning}
          notify={prefs.notify}
          onNotify={async (on) => {
            if (on) {
              if (!("Notification" in window)) return toast("This browser cannot show notifications.");
              const permission = Notification.permission === "default" ? await Notification.requestPermission() : Notification.permission;
              if (permission !== "granted") return toast("Notifications are blocked for this site. Allow them in the browser's site settings.");
            }
            setPrefs({ ...prefsRef.current, notify: on });
          }}
          onStop={() => abort.current?.abort()}
          onConnections={() => setSettings("connections")}
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
      {settings && (
        <SettingsDialog
          tab={settings}
          onTab={setSettings}
          onClose={() => setSettings(null)}
          connections={connections}
          onSetLevel={async (id, level) => {
            await api.setConnectionLevel(id, level);
            await refreshConnections();
          }}
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
          model={model}
          modelError={modelError}
          loadingModel={loadingModel}
          onRetryModel={() => void refreshModel()}
          onSaveModel={async (selection) => setModel(await api.selectModel(selection))}
          theme={theme}
          onTheme={setTheme}
          prefs={prefs}
          onPrefs={setPrefs}
        />
      )}
      <Toasts toasts={toasts} onDismiss={(id) => setToasts((t) => t.filter((x) => x.id !== id))} />
    </div>
  );
}
