# AskScout

AskScout is a full-stack AI assistant with two modes:

- **Ask**: upload a PDF and ask questions about it. Answers come from the document through a
  Retrieval-Augmented Generation (RAG) pipeline, with page citations. Without a document, it
  gives a general answer.
- **Agent**: give it a task and an optional URL. A tool-calling agent searches the web, opens
  pages, follows links and checks URLs through a Model Context Protocol (MCP) server, and
  streams each step to the UI.

Both modes support voice input and read-aloud answers.

## Features

- PDF question-answering with LangChain: chunking, OpenAI embeddings, an in-memory vector store
  (cached per file) and page-level citations.
- A web research agent over MCP tools: `search_web`, `fetch_url`, `find_in_page`,
  `extract_links` and `check_url`.
- Browser control in Agent mode (Playwright): give it a URL and a task like "search this site
  for pricing" and it opens the page in a headless browser, then clicks, types, selects and
  scrolls. Steps and a live screenshot show in the UI. Risky actions (buy, sign up, send,
  submit, delete, ...) pause for your **Allow / Decline**.
- Live agent steps over Server-Sent Events, with a Stop button.
- Markdown answers with sources, plus copy, read-aloud and regenerate actions.
- Voice mode (speech-to-text and text-to-speech), dark mode. Chat history is not saved; refreshing starts a new chat.

## Tech stack

| Layer | Tools |
|---|---|
| Frontend | React 18 (Create React App), Ant Design, Axios, react-markdown, react-speech-recognition, speak-tts |
| Backend | Python 3.10+, FastAPI, Uvicorn |
| RAG | LangChain (`langchain-openai`, `langchain-community`, `pypdf`) |
| Agent | Python `mcp` SDK (stdio), OpenAI tool calling, SerpAPI, httpx, BeautifulSoup, Playwright (Chromium) |

## Project structure

```
.
├── public/                 static assets (favicon, manifest)
├── src/                    React frontend
│   ├── App.js              layout, Ask/Agent modes, conversation state
│   ├── api.js              backend client (REST + agent event stream)
│   ├── useSpeech.js        text-to-speech hook
│   └── components/         ChatComponent, PdfUploader, RenderQA
└── server/                 Python backend
    ├── server.py           FastAPI app and routes
    ├── chat.py             Ask mode (RAG)
    ├── agent.py            Agent mode: tool-calling loop + MCP client
    ├── mcp_server.py       MCP server with the web tools
    ├── browser_tools.py    browser_* tools (Playwright) registered on the MCP server
    ├── requirements.txt
    └── .env.example        copy to .env and add your keys
```

## Getting started

Requirements: Node.js 18+, Python 3.10+ (the `mcp` package does not support older versions),
an [OpenAI API key](https://platform.openai.com/api-keys) and a
[SerpAPI key](https://serpapi.com/manage-api-key).

```bash
npm install                          # frontend dependencies
npm run setup:server                 # creates server/.venv, installs requirements + headless Chromium
cp server/.env.example server/.env   # then add OPENAI_API_KEY and SERPAPI_KEY
npm run dev                          # frontend on :3000, backend on :5001
```

`npm run setup:server` uses `python3.11`. If you have a different version (3.10 or newer),
create the virtual environment manually:
`cd server && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/playwright install --only-shell chromium`.

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/upload` | Multipart field `file` (PDF only). Sets the active document. |
| `GET` | `/status` | `{ "document": "file.pdf" \| null }` |
| `GET` | `/ask?question=...` | `{ "text", "sources", "mode": "document" \| "general" }` |
| `POST` | `/agent` | Body `{ "task", "url"? }`. Streams Server-Sent Events: `step`, `result`, `approval`, `approval_result`, `answer`, `error`. |
| `POST` | `/agent/approve` | Body `{ "approval_id", "approved" }`. Answers a paused risky browser action. |

## Agent safety

- Only public `http(s)` URLs are fetched; localhost, private networks and cloud metadata
  addresses are blocked, and every redirect is re-checked.
- The browser uses a fresh, isolated profile per run (no cookies or logins), and is closed when
  the run ends. Every request it makes passes the same public-address check. Downloads are disabled.
- The agent never fills in password or payment fields. Clicks or submits that could buy, sign up,
  post, send or delete need your approval in the UI (auto-declined after 5 minutes).
- Page size, request time and the number of agent steps are capped.
- Page content is treated as untrusted data; the agent is told never to follow instructions
  found in web pages.

## Tests

```bash
npm test
```

## Notes

- `server/.env` and `server/uploads/` are git-ignored. Never commit API keys or personal
  documents.
- This is a single-user demo: the active document is stored in server memory and shared by
  all clients.
