import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Button,
  ConfigProvider,
  Input,
  Layout,
  Segmented,
  Switch,
  Tooltip,
  theme as antdTheme,
} from "antd";
import {
  BulbOutlined,
  LinkOutlined,
  MessageOutlined,
  MoonOutlined,
  PlusOutlined,
  RobotOutlined,
  SunOutlined,
} from "@ant-design/icons";
import PdfUploader from "./components/PdfUploader";
import ChatComponent from "./components/ChatComponent";
import RenderQA from "./components/RenderQA";
import { ask, getStatus, runAgent } from "./api";
import useSpeech from "./useSpeech";
import "./App.css";

const { Header, Content, Footer } = Layout;

const STORAGE_KEY = "askscout.conversation.v2";
const THEME_KEY = "askscout.dark";
const MODE_KEY = "askscout.mode";

const SUGGESTIONS = {
  askDoc: [
    "Summarize this document",
    "What are the key takeaways?",
    "What problem does this document address?",
  ],
  ask: [
    "What is retrieval-augmented generation?",
    "Explain the Model Context Protocol simply",
    "Give me tips for writing a strong resume",
  ],
  agentUrl: [
    "Summarize what this page is about",
    "Is this site up? Check for broken or redirecting links",
    "Find pricing, contact info and key dates on this site",
  ],
  agent: [
    "What's the latest release of Python and what's new in it?",
    "Check if https://example.com is up and what it says",
    "Compare the pricing pages of Vercel and Netlify",
  ],
};

const loadConversation = () => {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY)) || [];
    // Anything still loading was interrupted by a refresh.
    return saved.map((e) =>
      e.answer?.status === "loading"
        ? { ...e, answer: { ...e.answer, status: "error", error: "Interrupted. Retry?" } }
        : e
    );
  } catch {
    return [];
  }
};

const newId = () => `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

export const Logo = ({ size = 32 }) => (
  <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
    <defs>
      <linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stopColor="#ffffff" />
        <stop offset="1" stopColor="#c7f9ff" />
      </linearGradient>
    </defs>
    <circle cx="32" cy="32" r="26" fill="none" stroke="url(#lg)" strokeWidth="5" />
    <ellipse cx="32" cy="32" rx="11" ry="26" fill="none" stroke="url(#lg)" strokeWidth="3" />
    <path d="M8 32h48" stroke="url(#lg)" strokeWidth="3" />
    <circle cx="48" cy="48" r="9" fill="#ffd166" />
    <text x="48" y="52" textAnchor="middle" fontSize="12" fontWeight="700" fill="#4834d4">
      ?
    </text>
  </svg>
);

const App = () => {
  const [conversation, setConversation] = useState(loadConversation);
  const [activeDoc, setActiveDoc] = useState(null);
  const [mode, setMode] = useState(() => localStorage.getItem(MODE_KEY) || "ask");
  const [agentUrl, setAgentUrl] = useState("");
  const [isDark, setIsDark] = useState(() => localStorage.getItem(THEME_KEY) === "1");
  const controllers = useRef({});
  const speech = useSpeech();

  const isLoading = conversation.some((e) => e.answer?.status === "loading");

  useEffect(() => {
    getStatus()
      .then((s) => setActiveDoc(s.document))
      .catch(() => {});
  }, []);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(conversation));
  }, [conversation]);

  useEffect(() => {
    localStorage.setItem(MODE_KEY, mode);
  }, [mode]);

  useEffect(() => {
    localStorage.setItem(THEME_KEY, isDark ? "1" : "0");
    document.body.classList.toggle("dark", isDark);
  }, [isDark]);

  const setAnswer = (id, update) =>
    setConversation((prev) =>
      prev.map((e) =>
        e.id === id
          ? { ...e, answer: typeof update === "function" ? update(e.answer) : update }
          : e
      )
    );

  const runAsk = useCallback(
    async (id, question) => {
      setAnswer(id, { status: "loading", kind: activeDoc ? "document" : "general" });
      try {
        const data = await ask(question);
        const answer = {
          status: "done",
          kind: data.mode,
          text: data.text,
          sources: data.sources || [],
        };
        setAnswer(id, answer);
        return answer;
      } catch (e) {
        const answer = { status: "error", error: e.message };
        setAnswer(id, answer);
        return answer;
      }
    },
    [activeDoc]
  );

  const runAgentEntry = useCallback(async (id, task, url) => {
    const controller = new AbortController();
    controllers.current[id] = controller;
    setAnswer(id, { status: "loading", steps: [] });
    let final = null;

    const onEvent = (ev) => {
      if (ev.type === "step") {
        setAnswer(id, (a) => ({
          ...a,
          steps: [...(a.steps || []), { id: ev.id, tool: ev.tool, args: ev.args, status: "running" }],
        }));
      } else if (ev.type === "result") {
        setAnswer(id, (a) => ({
          ...a,
          steps: (a.steps || []).map((s) =>
            s.id === ev.id ? { ...s, status: ev.ok ? "ok" : "failed", summary: ev.summary } : s
          ),
        }));
      } else if (ev.type === "answer") {
        final = { status: "done", text: ev.text, sources: ev.sources || [] };
        setAnswer(id, (a) => ({ ...a, ...final }));
      } else if (ev.type === "error") {
        final = { status: "error", error: ev.message };
        setAnswer(id, (a) => ({ ...a, ...final }));
      }
    };

    try {
      await runAgent({ task, url, onEvent, signal: controller.signal });
      if (!final) {
        final = { status: "error", error: "The agent stopped without an answer. Retry?" };
        setAnswer(id, (a) => ({ ...a, ...final }));
      }
    } catch (e) {
      final = {
        status: "error",
        error: e.name === "AbortError" ? "Stopped." : e.message,
      };
      setAnswer(id, (a) => ({
        ...a,
        ...final,
        steps: (a.steps || []).map((s) =>
          s.status === "running" ? { ...s, status: "failed", summary: "Stopped" } : s
        ),
      }));
    } finally {
      delete controllers.current[id];
    }
    return final;
  }, []);

  const handleAsk = async (question) => {
    const id = newId();
    const url = mode === "agent" ? agentUrl.trim() || null : null;
    setConversation((prev) => [
      ...prev,
      { id, mode, question, url, answer: { status: "loading" } },
    ]);
    return mode === "agent" ? runAgentEntry(id, question, url) : runAsk(id, question);
  };

  const handleRetry = (id) => {
    const entry = conversation.find((e) => e.id === id);
    if (!entry) return;
    if (entry.mode === "agent") runAgentEntry(id, entry.question, entry.url);
    else runAsk(id, entry.question);
  };

  const handleStop = (id) => controllers.current[id]?.abort();

  const newChat = () => {
    speech.stop();
    Object.values(controllers.current).forEach((c) => c.abort());
    setConversation([]);
  };

  const suggestions =
    mode === "agent"
      ? agentUrl.trim()
        ? SUGGESTIONS.agentUrl
        : SUGGESTIONS.agent
      : activeDoc
      ? SUGGESTIONS.askDoc
      : SUGGESTIONS.ask;

  const emptyText =
    mode === "agent"
      ? "Agent mode: give AskScout a task and, optionally, a URL. It will open pages, follow links, search the web and check things for you, step by step."
      : activeDoc
      ? `Ask mode: ask anything about ${activeDoc}.`
      : "Ask mode: upload a PDF above to ask about it, or just ask a question.";

  return (
    <ConfigProvider
      theme={{
        algorithm: isDark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
        token: {
          colorPrimary: "#6C5CE7",
          borderRadius: 12,
          fontFamily:
            "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
        },
      }}
    >
      <Layout className={`app-layout ${isDark ? "dark" : ""}`}>
        <Header className="app-header">
          <div className="brand">
            <Logo />
            <h1 className="app-title">AskScout</h1>
            <span className="app-subtitle">
              {mode === "agent" ? "Your web research agent" : "Ask your documents"}
            </span>
          </div>
          <div className="header-actions">
            <Tooltip title="Start a new chat">
              <Button
                ghost
                icon={<PlusOutlined />}
                onClick={newChat}
                disabled={!conversation.length}
                aria-label="New chat"
              >
                <span className="hide-sm">New chat</span>
              </Button>
            </Tooltip>
            <Tooltip title={isDark ? "Light mode" : "Dark mode"}>
              <Switch
                checked={isDark}
                onChange={setIsDark}
                checkedChildren={<MoonOutlined />}
                unCheckedChildren={<SunOutlined />}
                aria-label="Toggle dark mode"
              />
            </Tooltip>
          </div>
        </Header>

        <Content className="app-content">
          <div className={`card top-card ${mode === "ask" && !activeDoc ? "" : "compact"}`}>
            <Segmented
              className="mode-switch"
              value={mode}
              onChange={setMode}
              aria-label="Mode"
              options={[
                { value: "ask", label: "Ask", icon: <MessageOutlined /> },
                { value: "agent", label: "Agent", icon: <RobotOutlined /> },
              ]}
            />
            <div className="top-card-body">
              {mode === "ask" ? (
                <PdfUploader activeDoc={activeDoc} onUploaded={setActiveDoc} />
              ) : (
                <Input
                  className="agent-url"
                  prefix={<LinkOutlined />}
                  placeholder="Optional starting URL, e.g. https://example.com"
                  value={agentUrl}
                  onChange={(e) => setAgentUrl(e.target.value)}
                  allowClear
                  aria-label="Starting URL"
                />
              )}
            </div>
          </div>

          <div className="card qa-card">
            {conversation.length === 0 ? (
              <div className="empty-state">
                {mode === "agent" ? (
                  <RobotOutlined className="empty-icon" />
                ) : (
                  <BulbOutlined className="empty-icon" />
                )}
                <p>{emptyText}</p>
                <div className="suggestions">
                  {suggestions.map((s) => (
                    <Button key={s} shape="round" onClick={() => handleAsk(s)} disabled={isLoading}>
                      {s}
                    </Button>
                  ))}
                </div>
              </div>
            ) : (
              <RenderQA
                conversation={conversation}
                onRetry={handleRetry}
                onStop={handleStop}
                speech={speech}
              />
            )}
          </div>
        </Content>

        <Footer className="app-footer">
          <ChatComponent
            onAsk={handleAsk}
            isLoading={isLoading}
            mode={mode}
            hasDocument={!!activeDoc}
            speech={speech}
          />
        </Footer>
      </Layout>
    </ConfigProvider>
  );
};

export default App;
