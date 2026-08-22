# MAROS Meals

A free, Python-first microlearning platform. Long-form programming lectures
become **Meals** — short, focused lessons with voice, visuals, real code, and
practice.

> Instagram has Reels. YouTube has Shorts. MAROS has Meals.

Paste a lecture URL, get an ordered series of Meals that teach it end to end.

## This project vs. the MAROS backend

Two separate projects.

| | |
|---|---|
| **this repo** | the Meals product — web app, planner, renderer. Has its own `.env`. |
| **MAROS backend** | the Python/FastAPI source of truth. Separate checkout, separate `.env`. |

This project **reads** from the backend — chipper's cached Whisper model,
podcastengine's TTS and LLM router, and the transcripts under `outputs/` —
and never writes into that tree. Point `MAROS_ROOT` at your checkout.

## Setup

```bash
cp .env.example .env          # then fill in GROQ_API_KEY and MAROS_ROOT

python3 -m venv venv
venv/bin/pip install -r requirements.txt

cd web && npm install
cd ../meal-renderer && npm install
```

`ffmpeg` must be on PATH.

## Running it

The backend supplies transcription, so start it first, in the MAROS checkout:

```bash
venv/bin/python -m uvicorn main:app --port 8000
```

Then, here:

```bash
cd web && npm run dev          # http://localhost:5173
```

Open **/add**, paste a YouTube URL, press **Turn this into Meals**. Progress
shows on `/generating/:runId`; the finished Meals land in `/feed`.

Same thing from the command line:

```bash
cd meals
../venv/bin/python -u pipeline.py --url "https://youtu.be/..."
../venv/bin/python -u pipeline.py --job <job_id>     # already ingested
```

## The pipeline

```
INGEST    chipper transcribes the lecture
PLAN      read it, find what it teaches, design an ordered series   ← the only LLM stage
VERIFY    execute every code snippet for real
NARRATE   edge-tts, then Whisper forced alignment
RENDER    Motion Canvas -> FFmpeg -> 1080x1920 MP4
```

Only PLAN needs an API key. Everything after it is local and free, so a run
that gets past planning finishes with no quota left.

## Rules the code enforces

**Never claim code executed if it did not.** `verify.py` runs each snippet and
records what it actually printed; `validate.py` rejects a Meal whose terminal
is unverified; the renderer throws rather than draw one. Terminal output is a
recording, never authored text.

**One Meal, one learning objective.** The count follows the material — a
9-minute overview yields ~5 Meals, a 45-minute build yields more. Never padded
to hit a number.

**Reuse the teacher's analogies.** If the lecture explains something with a
comparison, that comparison is carried into the Meal verbatim rather than
replaced with an invented one.

**Timing is never hand-authored.** Each scene anchors to a verbatim phrase from
the script; forced alignment decides when it plays. Visuals cannot drift from
the voice.

## The AI does not generate pixels

The LLM's entire output is structured text: what to teach, what to say, what to
show, when. A deterministic engine draws it.

No Sora, Runway, Pika or Veo. No diffusion, no AI avatars, no voice cloning.
Same Meal JSON renders the identical video every time, for the cost of CPU.

## Layout

```
meals/          schema, planner, taxonomy, verify, validate, narrate, pipeline
meal-renderer/  Motion Canvas -> FFmpeg renderer (one renderer, many Meal files)
web/            React app: Meal feed, explore, lessons, tutor
clipper/        the older two-character explainer pipeline
docs/           architecture, product spec, API gaps
```

## Stack

React + TypeScript + Vite + Tailwind · Motion Canvas (MIT) · FFmpeg ·
edge-tts · faster-whisper · Groq for planning.

Remotion was evaluated and rejected: not open source, free only below four
employees, and it forbids derivative rendering products.
