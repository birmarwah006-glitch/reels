"""
End-to-end Meal pipeline: a lecture URL in, watchable Meals out.

    python3 pipeline.py --url "https://youtu.be/..."
    python3 pipeline.py --job <job_id>          # already-ingested lecture

Chains the five stages that were previously separate commands, and writes a
status file after every one so a UI can poll progress rather than watch a log:

    INGEST     POST /jobs/youtube, then wait for chipper's manifest
    PLAN       planner.py    transcript -> ordered Meal documents
    VERIFY     verify.py     actually runs each snippet, records real output
    NARRATE    narrate.py    edge-tts, then Whisper forced alignment
    RENDER     render.mjs    Motion Canvas -> FFmpeg -> 1080x1920 MP4

Only the PLAN stage needs the LLM. Everything after it is local and free, so a
run that gets past planning will finish even with no API quota left.

Status lives at meals/build/pipeline_{job_id}.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import env as project_env

HERE = Path(__file__).parent
MAROS_ROOT = project_env.MAROS_ROOT
RENDERER_DIR = project_env.RENDERER_DIR
BUILD_DIR = project_env.BUILD_DIR
CATALOGUE_DIR = project_env.CATALOGUE_DIR
OUT_DIR = project_env.OUT_DIR

API = project_env.MAROS_API

# This project's own interpreter, not MAROS's and not whatever launched us:
# the dev server spawns the pipeline with a plain python that has none of the
# dependencies.
VENV_PYTHON = project_env.PROJECT_ROOT / "venv" / "bin" / "python"
PYTHON = str(VENV_PYTHON if VENV_PYTHON.exists() else sys.executable)

STAGES = ["ingest", "plan", "verify", "narrate", "render"]


class Status:
    """A JSON file the UI polls. Written after every meaningful step, because
    a pipeline that takes twenty minutes is unusable if it is silent.

    Keyed on a caller-supplied run id when there is one: a UI needs a handle
    to poll from the moment it hits Start, and the job id does not exist until
    chipper has accepted the URL.
    """

    def __init__(self, job_id: str, url: str | None, run_id: str | None = None):
        self.run_id = run_id
        self.path = BUILD_DIR / f"pipeline_{run_id or job_id}.json"
        self.data = {
            "run_id": run_id,
            "job_id": job_id,
            # Recorded so a reader can tell "still working" from "died".
            # A killed or crashed run never gets to write a terminal state,
            # and without this the UI polls a stale file forever.
            "pid": os.getpid(),
            "url": url,
            "state": "running",
            "stage": "ingest",
            "stages": {s: {"state": "pending"} for s in STAGES},
            "meals": [],
            "error": None,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.write()

    def write(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2) + "\n")

    def stage(self, name: str, state: str, **extra) -> None:
        self.data["stage"] = name
        self.data["stages"][name] = {"state": state, **extra}
        self.write()
        detail = " ".join(f"{k}={v}" for k, v in extra.items())
        print(f"[pipeline] {name}: {state} {detail}".rstrip(), flush=True)

    def finish(self, state: str, error: str | None = None) -> None:
        self.data["state"] = state
        self.data["error"] = error
        self.data["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.write()


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 3600) -> tuple[int, str]:
    """Run a stage, capturing everything. -u keeps the child's output
    unbuffered so a captured log is not empty on a crash."""
    proc = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, timeout=timeout,
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def run_streaming(cmd: list[str], cwd: Path, on_line) -> tuple[int, str]:
    """Run a stage and hand each line to a callback as it arrives.

    Planning is by far the longest stage — it comprehends the lecture, designs
    the series, and then makes one call per Meal. Captured wholesale it looks
    frozen for minutes, which is exactly how it looked in the UI: "Finding the
    concepts" and nothing else. Streaming lets the status file report which
    Meal is being written.
    """
    proc = subprocess.Popen(
        cmd, cwd=str(cwd), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    captured: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        captured.append(line)
        # Echo it. Capturing silently meant a run that lost twelve of fourteen
        # sections left no trace of it anywhere: only the last 1,500 characters
        # survived, inside an exception message.
        print(line.rstrip(), flush=True)
        try:
            on_line(line.rstrip())
        except Exception:
            pass  # progress reporting must never break the run
    proc.wait()
    return proc.returncode, "".join(captured)


# ─────────────────────────────────────────────────────────────────────────
# Stages
# ─────────────────────────────────────────────────────────────────────────

def ingest_captions(url: str, status: Status) -> Path | None:
    """Try the publisher's own captions before transcribing anything.

    Whisper at chipper's `tiny` size renders technical speech as word salad,
    and transcription is also the slowest stage by an order of magnitude. When
    a video ships captions they are both faster and better, so they are tried
    first. Falls back silently — a video without captions is ordinary.
    """
    from transcript import fetch_youtube_captions

    status.stage("ingest", "running", url=url, source="captions")
    cookies = MAROS_ROOT / "config" / "cookies.txt"
    result = fetch_youtube_captions(url, cookies)
    if not result:
        print("[pipeline] no captions on this video — falling back to transcription",
              flush=True)
        return None

    title, text = result
    path = BUILD_DIR / f"captions_{abs(hash(url)) % (10 ** 10)}.txt"
    path.write_text(f"{title}\n\n{text}")
    status.stage("ingest", "done", source="captions",
                 words=len(text.split()), title=title[:60])
    return path


def ingest(url: str, status: Status) -> str:
    """Hand the URL to the existing backend and wait for chipper to finish.

    Uses the real endpoints rather than calling chipper directly, so the
    lecture also shows up everywhere else in the product.
    """
    import requests

    status.stage("ingest", "running", url=url)
    response = requests.post(f"{API}/jobs/youtube", json={"url": url}, timeout=120)
    response.raise_for_status()
    job_id = response.json()["job_id"]

    status.data["job_id"] = job_id
    status.write()
    print(f"[pipeline] job {job_id}", flush=True)

    # Poll the disk-backed route: /jobs/{id} is in-memory and 404s if the
    # server restarts mid-transcription, which for a long lecture is likely.
    deadline = time.time() + 3600
    while time.time() < deadline:
        try:
            modules = requests.get(f"{API}/modules/{job_id}", timeout=30)
            if modules.status_code == 200 and modules.json():
                status.stage("ingest", "done", job_id=job_id,
                             modules=len(modules.json()))
                return job_id
        except Exception:
            pass

        try:
            job = requests.get(f"{API}/jobs/{job_id}", timeout=30).json()
            if job.get("status") == "failed":
                raise RuntimeError(f"chipper failed: {job.get('error')}")
            status.data["stages"]["ingest"] = {
                "state": "running", "job_id": job_id,
                "chipper_status": job.get("status"), "progress": job.get("progress", 0),
            }
            status.write()
        except RuntimeError:
            raise
        except Exception:
            pass

        time.sleep(10)

    raise TimeoutError("chipper did not finish within an hour")


# Lines the planner prints that map onto visible progress.
PASS1 = re.compile(r"window (\d+)/(\d+)")
PASS2 = re.compile(r"series: (\d+) Meals")
PASS3 = re.compile(r"\[(\d+)/(\d+)\]\s+(.*)")


def plan(job_id: str, status: Status,
         transcript: Path | None = None, title: str = "") -> list[Path]:
    status.stage("plan", "running", step="reading the lecture")

    def progress(line: str) -> None:
        if m := PASS1.search(line):
            status.stage("plan", "running", step="reading the lecture",
                         window=f"{m.group(1)}/{m.group(2)}")
        elif m := PASS2.search(line):
            status.stage("plan", "running", step="designing the series",
                         planned=int(m.group(1)))
        elif m := PASS3.search(line):
            status.stage("plan", "running", step="writing the Meals",
                         written=f"{m.group(1)}/{m.group(2)}",
                         current=m.group(3)[:60])

    command = (
        [PYTHON, "-u", "planner.py", "--transcript", str(transcript),
         "--title", title, "--key", job_id]
        if transcript else
        [PYTHON, "-u", "planner.py", "--job", job_id]
    )
    code, log = run_streaming(command, HERE, progress)

    # Read the series the planner declares rather than diffing the catalogue
    # directory: on a resumed run every Meal already exists on disk, so a diff
    # finds nothing and the pipeline would wrongly conclude it produced none.
    pointer = BUILD_DIR / f"series_job_{job_id}.json"
    if not pointer.exists():
        raise RuntimeError(f"planner produced no series.\n{log[-1500:]}")

    series = json.loads(pointer.read_text())
    meals = [CATALOGUE_DIR / f"{mid}.json" for mid in series.get("meal_ids", [])]
    meals = [m for m in meals if m.exists()]

    if not meals:
        raise RuntimeError(f"planner produced no Meals.\n{log[-1500:]}")

    # A non-zero exit with Meals on disk means some authoring calls failed —
    # usually the rate limit. Partial output is still worth rendering.
    if code != 0:
        print(f"[pipeline] planner exited {code} but wrote {len(meals)} Meals; continuing",
              flush=True)

    failures = series.get("failures") or []
    status.stage("plan", "done", meals=len(meals),
                 **({"could_not_author": len(failures)} if failures else {}))
    return meals


def verify(meals: list[Path], status: Status) -> list[Path]:
    """Run every snippet for real. A Meal whose code fails is dropped, not
    fixed and not shipped — the rule is that a Meal never claims code ran when
    it did not."""
    status.stage("verify", "running", meals=len(meals))
    run([PYTHON, "-u", "verify.py", *[str(m) for m in meals]], cwd=HERE)

    good, rejected = [], []
    for path in meals:
        code, _ = run([PYTHON, "-u", "validate.py", str(path)], cwd=HERE)
        (good if code == 0 else rejected).append(path)

    if rejected:
        print(f"[pipeline] {len(rejected)} Meal(s) rejected by validation:", flush=True)
        for path in rejected:
            print(f"    {path.name}", flush=True)

    if not good:
        raise RuntimeError("every Meal failed validation")

    status.stage("verify", "done", passed=len(good), rejected=len(rejected))
    return good


def narrate_and_render(meals: list[Path], status: Status) -> list[str]:
    """Local and free: edge-tts, Whisper and FFmpeg. No API quota involved, so
    this stage completes even when the LLM budget is exhausted."""
    status.stage("narrate", "running", meals=len(meals))
    rendered: list[str] = []
    failures: list[str] = []

    for i, path in enumerate(meals, 1):
        meal_id = json.loads(path.read_text())["meal"]["id"]
        print(f"[pipeline]   ({i}/{len(meals)}) {meal_id}", flush=True)

        code, log = run([PYTHON, "-u", "narrate.py", str(path)], cwd=HERE, timeout=900)
        if code != 0:
            failures.append(f"{meal_id}: narrate failed")
            print(f"    narrate failed: {log[-400:]}", flush=True)
            continue

        code, log = run(["node", "render.mjs", str(path.resolve())],
                        cwd=RENDERER_DIR, timeout=1800)
        if code != 0 or not (OUT_DIR / f"{meal_id}.mp4").exists():
            failures.append(f"{meal_id}: render failed")
            print(f"    render failed: {log[-400:]}", flush=True)
            continue

        rendered.append(meal_id)
        status.data["meals"] = rendered
        status.data["stages"]["narrate"] = {
            "state": "running", "done": len(rendered), "total": len(meals),
        }
        status.write()

    if not rendered:
        raise RuntimeError("no Meal rendered successfully")

    status.stage("narrate", "done", rendered=len(rendered))
    status.stage("render", "done", rendered=len(rendered),
                 failed=len(failures))
    return rendered


# ─────────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Lecture URL in, Meals out.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="YouTube URL to ingest")
    src.add_argument("--job", help="job id of an already-ingested lecture")
    ap.add_argument("--run-id", help="caller-supplied handle for the status file")
    args = ap.parse_args()

    status = Status(args.job or "pending", args.url, args.run_id)

    try:
        transcript_path, title = None, ""
        if args.url:
            transcript_path = ingest_captions(args.url, status)
            if transcript_path:
                # Captions carry their own title on the first line.
                head = transcript_path.read_text().split("\n\n", 1)
                title = head[0].strip()
                job_id = f"cap_{abs(hash(args.url)) % (10 ** 10)}"
                status.data["job_id"] = job_id
                status.write()
            else:
                job_id = ingest(args.url, status)
        else:
            job_id = args.job
            status.stage("ingest", "done", job_id=job_id, note="already ingested")
            status.data["job_id"] = job_id
            if not status.run_id:
                status.path.unlink(missing_ok=True)
                status.path = BUILD_DIR / f"pipeline_{job_id}.json"
            status.write()

        meals = plan(job_id, status, transcript_path, title)
        meals = verify(meals, status)
        rendered = narrate_and_render(meals, status)

        status.finish("done")
        print(f"\n[pipeline] {len(rendered)} Meal(s) ready:", flush=True)
        for meal_id in rendered:
            print(f"    {meal_id}", flush=True)
        print("[pipeline] open http://localhost:5173/feed", flush=True)
        return 0

    except Exception as e:
        status.finish("failed", str(e))
        print(f"\n[pipeline] FAILED at {status.data['stage']}: {e}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
