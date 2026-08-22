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
    """Run a stage, streaming nothing but capturing everything. -u keeps the
    child's output unbuffered so a captured log is not empty on a crash."""
    proc = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, timeout=timeout,
    )
    return proc.returncode, (proc.stdout + proc.stderr)


# ─────────────────────────────────────────────────────────────────────────
# Stages
# ─────────────────────────────────────────────────────────────────────────

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


def plan(job_id: str, status: Status) -> list[Path]:
    status.stage("plan", "running")

    code, log = run([PYTHON, "-u", "planner.py", "--job", job_id], cwd=HERE)

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
        if args.url:
            job_id = ingest(args.url, status)
        else:
            job_id = args.job
            status.stage("ingest", "done", job_id=job_id, note="already ingested")
            status.data["job_id"] = job_id
            if not status.run_id:
                status.path.unlink(missing_ok=True)
                status.path = BUILD_DIR / f"pipeline_{job_id}.json"
            status.write()

        meals = plan(job_id, status)
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
