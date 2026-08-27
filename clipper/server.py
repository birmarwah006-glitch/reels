"""
Local server: paste a YouTube URL into the browser page this serves, it
downloads + transcribes + scripts + narrates + renders one reel per chunk,
and prints progress straight into THIS terminal (the same prints
run_clipper.py always made — an HTTP trigger sits in front of it, nothing
about the pipeline itself changed).

Run:
    venv/bin/pip install -r requirements.txt
    venv/bin/uvicorn clipper.server:app --reload

Then open http://127.0.0.1:8000 in a browser.

Requires the same env as run_clipper.py: MAROS_ROOT pointing at a MAROS
checkout, and an LLM key (CEREBRAS_API_KEY / GROQ_API_KEY) reachable
through MAROS's .env, since generate_conversation() still calls chipper's
LLM router. Character PNGs and the classroom background must also exist
under clipper/assets/ (see README).
"""

import threading
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from clipper.run_clipper import run_module
from clipper.youtube_ingest import build_modules_from_youtube

app = FastAPI(title="clipper")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

FRONTEND_HTML = (Path(__file__).parent / "frontend.html").read_text()


class ProcessRequest(BaseModel):
    youtube_url: str
    chunk_minutes: float = 4.0


@app.get("/", response_class=HTMLResponse)
def index():
    return FRONTEND_HTML


def _run_job(job_id: str, youtube_url: str, chunk_minutes: float):
    print(f"\n[server] job {job_id} started for {youtube_url}")
    try:
        modules = build_modules_from_youtube(youtube_url, chunk_minutes)
        if not modules:
            print(f"[server] job {job_id}: no transcript segments produced, nothing to render")
            return
        results = []
        for m in modules:
            try:
                result = run_module(job_id, m)
                if result:
                    results.append(result)
            except Exception as e:
                print(f"[server] job {job_id} module {m['module_id']} FAILED: {e}")
                traceback.print_exc()
        print(f"[server] job {job_id} DONE — {len(results)}/{len(modules)} reel(s) rendered")
    except Exception:
        print(f"[server] job {job_id} FAILED before rendering started")
        traceback.print_exc()


@app.post("/process")
def process(req: ProcessRequest):
    if "youtube.com" not in req.youtube_url and "youtu.be" not in req.youtube_url:
        raise HTTPException(status_code=400, detail="that doesn't look like a YouTube URL")

    job_id = uuid.uuid4().hex[:8]
    thread = threading.Thread(
        target=_run_job, args=(job_id, req.youtube_url, req.chunk_minutes), daemon=True,
    )
    thread.start()
    return {"job_id": job_id, "status": "started - watch your server terminal"}
