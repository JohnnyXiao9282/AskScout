import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

jest.mock("./api", () => ({
  getStatus: jest.fn(),
  uploadPdf: jest.fn(),
  ask: jest.fn(),
  runAgent: jest.fn(),
}));
jest.mock("react-markdown", () => ({ children }) => <div>{children}</div>);

jest.mock("speak-tts", () =>
  jest.fn().mockImplementation(() => ({
    hasBrowserSupport: () => false,
    init: () => Promise.resolve({}),
    speak: () => Promise.resolve(),
    cancel: () => {},
  }))
);

jest.mock("react-speech-recognition", () => ({
  __esModule: true,
  default: { startListening: jest.fn(), stopListening: jest.fn() },
  useSpeechRecognition: () => ({
    transcript: "",
    listening: false,
    resetTranscript: jest.fn(),
    browserSupportsSpeechRecognition: false,
    isMicrophoneAvailable: true,
  }),
}));

beforeAll(() => {
  window.matchMedia =
    window.matchMedia ||
    (() => ({
      matches: false,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
  Element.prototype.scrollIntoView = jest.fn();
});

const api = require("./api");

beforeEach(() => {
  localStorage.clear();
  jest.clearAllMocks();
  api.getStatus.mockResolvedValue({ document: null });
});

const renderApp = () => {
  const App = require("./App").default;
  return render(<App />);
};

test("renders title in Ask mode with suggestions", async () => {
  renderApp();
  expect(screen.getByText("AskScout")).toBeInTheDocument();
  expect(await screen.findByText("What is retrieval-augmented generation?")).toBeInTheDocument();
});

test("Ask mode shows a single answer from the document with page sources", async () => {
  api.getStatus.mockResolvedValue({ document: "lean.pdf" });
  api.ask.mockResolvedValue({
    text: "Doc answer",
    mode: "document",
    sources: [{ page: 3, snippet: "build measure learn" }],
  });
  renderApp();
  expect(await screen.findByText("lean.pdf")).toBeInTheDocument();

  await act(async () => {
    fireEvent.click(screen.getByText("Summarize this document"));
  });

  expect(await screen.findByText("Doc answer")).toBeInTheDocument();
  expect(screen.getByText("📄 From your document")).toBeInTheDocument();
  expect(screen.getByText("Page 3:")).toBeInTheDocument();
  expect(api.runAgent).not.toHaveBeenCalled();
  expect(screen.queryByText(/From the web/)).not.toBeInTheDocument();
});

test("Agent mode streams tool steps and a final answer", async () => {
  api.runAgent.mockImplementation(async ({ onEvent }) => {
    onEvent({ type: "step", id: "1", tool: "fetch_url", args: { url: "https://example.com" } });
    onEvent({ type: "result", id: "1", tool: "fetch_url", ok: true, summary: "Read “Example Domain”" });
    onEvent({
      type: "answer",
      text: "The site is up.",
      sources: [{ title: "Example Domain", link: "https://example.com" }],
    });
  });
  renderApp();

  fireEvent.click(await screen.findByText("Agent"));
  fireEvent.change(screen.getByLabelText("Starting URL"), {
    target: { value: "https://example.com" },
  });
  await act(async () => {
    fireEvent.click(screen.getByText("Summarize this page and check for broken links"));
  });

  expect(await screen.findByText("The site is up.")).toBeInTheDocument();
  expect(screen.getByText("Opened example.com")).toBeInTheDocument();
  expect(screen.getByText("Read “Example Domain”")).toBeInTheDocument();
  expect(screen.getByText("Example Domain")).toHaveAttribute("href", "https://example.com");
  expect(api.runAgent).toHaveBeenCalledWith(
    expect.objectContaining({ task: "Summarize this page and check for broken links", url: "https://example.com" })
  );
  expect(api.ask).not.toHaveBeenCalled();
});

test("shows an error with retry when a request fails", async () => {
  api.ask.mockRejectedValue(new Error("Model failed"));
  renderApp();
  await act(async () => {
    fireEvent.click(await screen.findByText("What is retrieval-augmented generation?"));
  });
  await waitFor(() => expect(screen.getByText("Model failed")).toBeInTheDocument());
  expect(screen.getByText("Retry")).toBeInTheDocument();
});
