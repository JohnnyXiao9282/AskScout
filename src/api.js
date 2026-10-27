import axios from "axios";

export const DOMAIN = process.env.REACT_APP_DOMAIN || "http://localhost:5001";

const errorMessage = (error) =>
  error?.response?.data?.detail ||
  (error?.code === "ERR_NETWORK"
    ? "Can't reach the server. Is the backend running on port 5001?"
    : error?.message) ||
  "Something went wrong.";

export const getStatus = async () => {
  const { data } = await axios.get(`${DOMAIN}/status`);
  return data; // { document: string | null }
};

export const uploadPdf = async (file) => {
  const formData = new FormData();
  formData.append("file", file);
  try {
    return await axios.post(`${DOMAIN}/upload`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  } catch (error) {
    throw new Error(errorMessage(error));
  }
};

/** Ask mode: RAG over the uploaded PDF (or a general answer if none). */
export const ask = async (question) => {
  try {
    const { data } = await axios.get(`${DOMAIN}/ask`, { params: { question } });
    return data; // { text, sources, mode }
  } catch (error) {
    throw new Error(errorMessage(error));
  }
};

/**
 * Agent mode: streams events from POST /agent (Server-Sent Events).
 * onEvent is called for each {type: "step" | "result" | "answer" | "error"}.
 */
export const runAgent = async ({ task, url, onEvent, signal }) => {
  let res;
  try {
    res = await fetch(`${DOMAIN}/agent`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task, url: url || null }),
      signal,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new Error(
      "Can't reach the server. Is the backend running on port 5001?"
    );
  }
  if (!res.ok || !res.body) {
    let detail = `Server error (${res.status})`;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : detail;
    } catch {}
    throw new Error(detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(5).trim()));
      } catch {}
    }
  }
};

/** Approve or decline a risky browser action the agent paused on. */
export const approveAction = async (approvalId, approved) => {
  const res = await fetch(`${DOMAIN}/agent/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approval_id: approvalId, approved }),
  });
  if (!res.ok) throw new Error("That request expired.");
};
