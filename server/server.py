"""AskScout backend (FastAPI).

Ask mode  (RAG only):
  POST /upload   multipart field "file" (PDF only) -> plain text "<path> upload successfully."
  GET  /status   -> {"document": filename | null}
  GET  /ask      ?question=...  -> {"text", "sources", "mode": "document" | "general"}

Agent mode (MCP web tools only):
  POST /agent    {"task": str, "url": str | null}
                 -> text/event-stream of JSON events (step / result / answer / error)
"""
import json
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import PlainTextResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

import chat as rag  # noqa: E402
from agent import close_client, resolve_approval, run_agent  # noqa: E402

PORT = 5001
UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

file_path: str | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await close_client()


app = FastAPI(title="AskScout", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


# ----------------------------- Ask mode ----------------------------------- #
@app.post("/upload", response_class=PlainTextResponse)
async def upload(file: UploadFile = File(...)):
    global file_path
    name = os.path.basename(file.filename or "")
    if not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    dest = UPLOAD_DIR / name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    rag.forget(str(dest))
    file_path = str(dest)
    return f"{file_path} upload successfully."


@app.get("/status")
async def status():
    active = file_path if file_path and os.path.exists(file_path) else None
    return {"document": os.path.basename(active) if active else None}


@app.get("/ask")
async def ask(question: str):
    try:
        return await rag.ask(file_path, question)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e


# ----------------------------- Agent mode --------------------------------- #
class AgentRequest(BaseModel):
    task: str = Field(min_length=1, max_length=4000)
    url: str | None = Field(default=None, max_length=2000)


@app.post("/agent")
async def agent(req: AgentRequest):
    async def events():
        async for event in run_agent(req.task.strip(), (req.url or "").strip() or None):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class ApprovalRequest(BaseModel):
    approval_id: str = Field(min_length=1, max_length=100)
    approved: bool


@app.post("/agent/approve")
async def agent_approve(req: ApprovalRequest):
    if not resolve_approval(req.approval_id, req.approved):
        raise HTTPException(404, "No pending approval with that id (it may have expired).")
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    print(f"Server is running on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
