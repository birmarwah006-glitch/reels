# clipper

Two-character comedic explainer reels for MAROS.

Reads a finished Chipper manifest (or a single past-year question) and renders
a ~50 second 9:16 video in which two characters work through the material:

- **CHARACTER_A** — hyper-intelligent, precise, slightly condescending. Walks a
  concrete worked example, grounded in the lecture transcript.
- **CHARACTER_B** — well-meaning, dim, and *confidently wrong*. His errors are
  the mistakes real students actually make and lose marks for, not random
  non-sequiturs. By the last line he restates the right answer in his own dumb
  words.

Static PNG cutouts, no rigging or lip-sync. Fully local: MoviePy + ffmpeg +
edge-tts. No paid services.

## Two modes

**Module mode** — one reel per module of a Chipper lecture manifest:

```bash
python -m clipper.run_clipper <job_id>
python -m clipper.run_clipper <job_id> --module 4
```

**PYQ mode** — the pair solve one real past-year exam question out loud:

```bash
python -m clipper.run_pyq 2017-ct1-2
python -m clipper.run_pyq --featured
python -m clipper.run_pyq --concept paging --limit 3
```

Output lands in `clipper/output/`, with a JSON sidecar carrying the script,
concept tags, matched question ids and durations.

## Setup

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

`clipper` imports a few helpers from MAROS's `chipper.py` — the LLM router,
the JSON fence stripper, the timestamp converters — and reads manifests from
its `outputs/` directory. **MAROS is read-only**: nothing here writes to that
tree. Point at your checkout with:

```bash
export MAROS_ROOT=/path/to/MAROS       # defaults to ~/Desktop/MAROS
```

An LLM key must be reachable through MAROS's `.env` (`CEREBRAS_API_KEY` and/or
`GROQ_API_KEY`), since the script generator calls chipper's router.

## Character art

**Not included in this repo.** The pipeline takes any tight-cropped
transparent RGBA PNG. Supply two and drop them here:

```
clipper/assets/characters/character_a.png     # the smart one
clipper/assets/characters/character_b.png     # the dim one
```

To cut a background off an image you have the rights to use:

```bash
pip install rembg
rembg i raw.png clipper/assets/characters/character_a.png
```

Use original characters or art you are licensed for. Do not ship third-party
copyrighted characters in a distributed build.

A background is expected at `clipper/assets/backgrounds/classroom.png` — any
static 9:16 image works.

## Voices

edge-tts, cast for contrast rather than accent alone:

| | Voice | Prosody |
|---|---|---|
| A | `en-GB-ThomasNeural` | rate +12%, pitch +18Hz — clipped and fast |
| B | `en-US-GuyNeural` | rate -8%, pitch -25Hz, volume +15% — slow, low, loud |

Override with `CLIPPER_VOICE_A` / `CLIPPER_VOICE_B`. gTTS is the offline
fallback if edge-tts can't reach the network.

These are original synthetic voices. This project deliberately does **not**
clone any existing voice performance.

## Layout

```
clipper/
  generate_script.py    transcript or PYQ -> A/B dialogue JSON
  generate_audio.py     dialogue -> per-line TTS stems
  build_reel.py         stems + PNGs -> 9:16 mp4
  pyq_matcher.py        PYQ pool + concept tagging
  run_clipper.py        module-mode orchestrator
  run_pyq.py            PYQ-mode orchestrator
  data/pyq_pool.json    93 past-year questions, concept-tagged
```

Concept tagging reuses prep mode's own tagger so a module and a question are
tagged by the same rules, rather than introducing a second tagger.

## Known issues

- **Concept tagger has no "none" escape.** The taxonomy is Operating-Systems
  only, and the keyword fallback hard-defaults to `processes`. A non-OS
  lecture gets mis-tagged and pulls irrelevant questions. The script prompt
  tells the model to ignore questions that don't fit, which holds in practice,
  but the tagger should be able to return nothing instead.
- **`gpt-oss-120b` returns empty content on long prompts.** It is a reasoning
  model, and chipper's Groq branch sends no `max_tokens` or `reasoning_effort`,
  so it spends its budget on hidden reasoning and Groq's JSON validator 400s.
  `_llm_json()` retries with `reasoning_effort: "low"`, which works but is
  flaky — sometimes taking two attempts. The real fix is two lines in
  `chipper.py`.
- **Script quality varies run to run.** Some takes produce a tight arc; others
  give CHARACTER_A a run-on list and CHARACTER_B a throwaway closing line.
  Worth re-rolling a reel that lands badly.
