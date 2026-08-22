"""
Environment for the MAROS Meals project.

This project is SEPARATE from the MAROS backend. It has its own .env here, and
it does not read MAROS's. What it does need from MAROS is code and data —
chipper's Whisper model, podcastengine's TTS and LLM router, and the
transcripts under outputs/ — so MAROS_ROOT points at that checkout.

That relationship is strictly one-way and read-only. Nothing here writes into
the MAROS tree.

Importing this module has two side effects, both deliberate and both required
before any MAROS module is imported:

  1. Loads ./.env into the process environment. MAROS's podcastengine reads
     GROQ_API_KEY at import time and never calls load_dotenv itself, so a
     module imported before this one sees an empty key and gets a 401.

  2. Repairs GROQ_MODEL if it names a model the account cannot see. Groq
     namespaces its ids: `openai/gpt-oss-120b`, not `gpt-oss-120b`. The bare
     form returns a 404 that reads like a deprecation rather than a typo.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# This project's own .env — not MAROS's.
load_dotenv(PROJECT_ROOT / ".env")

MAROS_ROOT = Path(
    os.getenv("MAROS_ROOT", Path.home() / "Desktop" / "MAROS")
).expanduser()

MAROS_API = os.getenv("MAROS_API", "http://127.0.0.1:8000")
MEAL_VOICE = os.getenv("MEAL_VOICE", "en-US-BrianNeural")

MEALS_DIR = PROJECT_ROOT / "meals"
CATALOGUE_DIR = MEALS_DIR / "catalogue"
BUILD_DIR = MEALS_DIR / "build"
OUT_DIR = MEALS_DIR / "out"
RENDERER_DIR = PROJECT_ROOT / "meal-renderer"

for directory in (BUILD_DIR, OUT_DIR, CATALOGUE_DIR):
    directory.mkdir(parents=True, exist_ok=True)


def require_maros() -> Path:
    """Make MAROS importable, with a message that says what to do if it is not.

    A missing checkout is the most likely first-run failure, so it fails here
    with an explanation rather than deeper down as an ImportError.
    """
    if not (MAROS_ROOT / "podcastengine.py").exists():
        raise SystemExit(
            f"[meals] Cannot find the MAROS backend at {MAROS_ROOT}\n"
            f"        This project reuses its transcription, TTS and LLM router.\n"
            f"        Set MAROS_ROOT in .env to your MAROS checkout."
        )
    if str(MAROS_ROOT) not in sys.path:
        sys.path.insert(0, str(MAROS_ROOT))
    return MAROS_ROOT


def repair_groq_model() -> None:
    """Verify GROQ_MODEL against the account's live model list, and fix it.

    Repairs this process's environment only — no file is written, and MAROS's
    own .env is never touched.
    """
    model = os.getenv("GROQ_MODEL", "")
    key = os.getenv("GROQ_API_KEY", "")
    if not model or not key:
        return

    try:
        import requests
        available = {
            m["id"] for m in requests.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=15,
            ).json().get("data", [])
        }
    except Exception:
        return  # offline or rate limited; let the real call report it

    if not available or model in available:
        return

    candidates = [c for c in available
                  if c == f"openai/{model}" or c.rsplit("/", 1)[-1] == model]
    if candidates:
        fixed = candidates[0]
        print(f"[meals] GROQ_MODEL={model!r} is not a model Groq recognises — "
              f"using {fixed!r} for this run.")
        print(f"[meals] Fix it permanently: set GROQ_MODEL={fixed} in .env")
        os.environ["GROQ_MODEL"] = fixed
    else:
        print(f"[meals] WARNING: GROQ_MODEL={model!r} is not in this account's "
              f"model list: {sorted(available)}")


def bootstrap() -> Path:
    """Everything that must happen before importing a MAROS module."""
    root = require_maros()
    repair_groq_model()
    return root
