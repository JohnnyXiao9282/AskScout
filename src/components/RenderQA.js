import React, { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import { Alert, Button, Spin, Tag, Tooltip, message } from "antd";
import {
  CheckCircleFilled,
  CloseCircleFilled,
  CopyOutlined,
  LoadingOutlined,
  ReloadOutlined,
  SoundOutlined,
  StopOutlined,
} from "@ant-design/icons";

const short = (url = "") => {
  try {
    const u = new URL(/^https?:\/\//i.test(url) ? url : `https://${url}`);
    const path = u.pathname === "/" ? "" : u.pathname;
    const s = u.hostname.replace(/^www\./, "") + path;
    return s.length > 60 ? `${s.slice(0, 57)}…` : s;
  } catch {
    return url;
  }
};

const TOOLS = {
  search_web: { icon: "🔎", text: (a) => `Searched “${a.query}”` },
  fetch_url: { icon: "📄", text: (a) => `Opened ${short(a.url)}` },
  find_in_page: {
    icon: "🔍",
    text: (a) => `Looked for ${(a.keywords || []).map((k) => `“${k}”`).join(", ")} on ${short(a.url)}`,
  },
  extract_links: { icon: "🔗", text: (a) => `Listed links on ${short(a.url)}` },
  check_url: { icon: "🩺", text: (a) => `Checked ${short(a.url)}` },
};

const describe = (step) => {
  const t = TOOLS[step.tool];
  return t
    ? { icon: t.icon, text: t.text(step.args || {}) }
    : { icon: "🛠️", text: `${step.tool}(${JSON.stringify(step.args)})` };
};

const Steps = ({ steps, running }) => {
  if (!steps?.length) return null;
  return (
    <details className="agent-steps" open={running}>
      <summary>
        {running ? "Working" : "Used"} {steps.length} tool step
        {steps.length > 1 ? "s" : ""}
      </summary>
      <ol>
        {steps.map((s) => {
          const d = describe(s);
          return (
            <li key={s.id} className={`step step-${s.status}`}>
              <span className="step-status" aria-label={s.status}>
                {s.status === "running" ? (
                  <LoadingOutlined />
                ) : s.status === "ok" ? (
                  <CheckCircleFilled />
                ) : (
                  <CloseCircleFilled />
                )}
              </span>
              <span className="step-icon" aria-hidden="true">
                {d.icon}
              </span>
              <span className="step-body">
                <span className="step-text">{d.text}</span>
                {s.summary && <span className="step-summary">{s.summary}</span>}
              </span>
            </li>
          );
        })}
      </ol>
    </details>
  );
};

const Sources = ({ sources }) => {
  if (!sources?.length) return null;
  return (
    <details className="sources">
      <summary>
        {sources.length} source{sources.length > 1 ? "s" : ""}
      </summary>
      <ol>
        {sources.map((s, i) =>
          s.link ? (
            <li key={s.link}>
              <a href={s.link} target="_blank" rel="noopener noreferrer">
                {s.title || short(s.link)}
              </a>
            </li>
          ) : (
            <li key={i}>
              {s.page != null && <strong>Page {s.page}: </strong>}
              <span className="source-snippet">“{s.snippet}…”</span>
            </li>
          )
        )}
      </ol>
    </details>
  );
};

const LABELS = {
  document: "📄 From your document",
  general: "💬 General answer",
  agent: "🤖 Agent",
};

const AnswerCard = ({ entry, onRetry, onStop, speech }) => {
  const { answer = {}, mode } = entry;
  const { status } = answer;
  const isAgent = mode === "agent";
  const label = isAgent ? LABELS.agent : LABELS[answer.kind] || "💬 Answer";

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(answer.text);
      message.success("Copied");
    } catch {
      message.error("Couldn't copy");
    }
  };

  return (
    <div className={`answer-card ${isAgent ? "answer-agent" : "answer-ask"}`}>
      <div className="answer-label">{label}</div>

      {isAgent && <Steps steps={answer.steps} running={status === "loading"} />}

      {status === "loading" && (
        <div className="answer-loading">
          <Spin size="small" />
          <span>
            {isAgent
              ? answer.steps?.length
                ? "Working on it…"
                : "Planning…"
              : answer.kind === "document"
              ? "Reading your document…"
              : "Thinking…"}
          </span>
          {isAgent && (
            <Button size="small" icon={<StopOutlined />} onClick={onStop}>
              Stop
            </Button>
          )}
        </div>
      )}

      {status === "error" && (
        <Alert
          type="error"
          showIcon
          message={answer.error}
          action={
            <Button size="small" icon={<ReloadOutlined />} onClick={onRetry}>
              Retry
            </Button>
          }
        />
      )}

      {status === "done" && (
        <>
          <div className="answer-body">
            <ReactMarkdown linkTarget="_blank">{answer.text || ""}</ReactMarkdown>
          </div>
          <Sources sources={answer.sources} />
          <div className="answer-actions">
            <Tooltip title="Copy">
              <Button
                type="text"
                size="small"
                icon={<CopyOutlined />}
                onClick={copy}
                aria-label="Copy answer"
              />
            </Tooltip>
            {speech?.ready && (
              <Tooltip title={speech.speaking ? "Stop" : "Read aloud"}>
                <Button
                  type="text"
                  size="small"
                  icon={speech.speaking ? <StopOutlined /> : <SoundOutlined />}
                  onClick={() =>
                    speech.speaking ? speech.stop() : speech.speak(answer.text)
                  }
                  aria-label={speech.speaking ? "Stop reading" : "Read answer aloud"}
                />
              </Tooltip>
            )}
            <Tooltip title="Regenerate">
              <Button
                type="text"
                size="small"
                icon={<ReloadOutlined />}
                onClick={onRetry}
                aria-label="Regenerate answer"
              />
            </Tooltip>
          </div>
        </>
      )}
    </div>
  );
};

const RenderQA = ({ conversation, onRetry, onStop, speech }) => {
  const endRef = useRef(null);

  // Keep the newest message in view.
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [conversation]);

  return (
    <div className="qa-list">
      {conversation.map((entry) => (
        <div key={entry.id} className="qa-entry">
          <div className="user-row">
            <div className="user-bubble">
              <Tag className="mode-tag" bordered={false}>
                {entry.mode === "agent" ? "Agent" : "Ask"}
              </Tag>
              {entry.question}
              {entry.url && <div className="user-url">🔗 {entry.url}</div>}
            </div>
          </div>
          <AnswerCard
            entry={entry}
            speech={speech}
            onRetry={() => onRetry(entry.id)}
            onStop={() => onStop(entry.id)}
          />
        </div>
      ))}
      <div ref={endRef} />
    </div>
  );
};

export default RenderQA;
