# MAROS Meals — Architecture, Reuse Audit, and Dependency Licences

Written for §39. Inspection and design only. Nothing was deleted, and the
Python backend was not modified.

---

## 1. Dependency licence review (§30)

Checked against the live npm registry before any install.

| Package | Version | Licence | Last stable publish | Verdict |
|---|---|---|---|---|
| `monaco-editor` | 0.56.0 | **MIT** | 2026-07-20 | **Safe.** Microsoft-maintained, actively released. |
| `@motion-canvas/core` | 3.17.2 | **MIT** | 2025-02-16 | Usable, **maintenance flag** — see below. |
| `@motion-canvas/2d` | 3.17.2 | **MIT** | 2025-02-16 | Same. Ships a built-in code node. |
| `@motion-canvas/ffmpeg` | 3.17.2 | **MIT** | 2025-02-16 | Headless render to video. |
| `remotion` | 4.0.514 | **Proprietary** | 2026-08-20 | **Do not use.** See below. |

### Remotion — recommend against

Remotion is not open source. Its licence is a two-tier company licence:

> You are eligible to use Remotion for free if you are: an individual, a
> for-profit organization **with up to 3 employees**, a non-profit, or
> evaluating whether Remotion is a good fit and are not yet using it in a
> commercial way.

MAROS is a commercial product with a stated monetisation path (contextual
advertising, sponsored Meals, creator tools, institutional products). The
moment MAROS is a for-profit entity with four or more employees, a **paid
company licence** is required, and the licence terms change again in Remotion
5.0.

There is a second clause that matters directly here:

> It is not allowed to copy or modify Remotion code for the purpose of
> selling, renting, licensing, relicensing, or sublicensing your own derivate.

A rendering engine is close to the core of what MAROS sells. Per §12 — *"Check
its CURRENT LICENSE and commercial-use requirements before using it. If the
licensing is unsuitable for MAROS, do not use it"* — **Remotion is not
adopted.** Monaco + Motion Canvas + FFmpeg, all MIT, are sufficient.

### Motion Canvas — maintenance flag

Latest stable `3.17.2` was published **2025-02-16**; the only newer release is
`3.18.0-alpha.0`. That is roughly eighteen months without a stable release.

It is MIT, so this is a risk to accept knowingly rather than a blocker: the
code can be vendored or forked if it stalls. It is recorded here so the choice
is deliberate.

Useful discovery: `@motion-canvas/2d` already depends on `@codemirror/language`,
`@lezer/highlight` and `code-fns` — it **ships a code node with syntax
highlighting and code-diff/typing animation built in**. That matters for the
next section.

---

## 2. The Monaco / Motion Canvas conflict — needs a decision

§31 places Monaco Editor and Motion Canvas inside a single "RENDERER" box.
**They cannot share one scene graph.**

- **Monaco is a DOM editor.** It renders HTML elements with CSS, and expects a
  live document with layout, scrolling, and its own measurement pass.
- **Motion Canvas 2d renders to an HTML5 canvas** scene graph of its own nodes.

There is no supported way to mount Monaco's DOM *inside* a Motion Canvas
canvas node. Any design that assumes it will fail late, after the visual
component library is already written against it.

Three ways to resolve it:

### Option A — Two render surfaces, composited by FFmpeg
Motion Canvas renders diagram scenes; a separate headless-browser DOM page
renders Monaco scenes; FFmpeg concatenates them into one Meal.

*Cost:* two pipelines, two visual languages to keep consistent, two sets of
timing code. Cross-fading between a canvas scene and a DOM scene is
frame-accurate only if both are rendered at identical fps and colour.

### Option B — One DOM renderer, no Motion Canvas
Headless Chromium plus Playwright frame capture. Monaco works natively.
Diagrams are SVG/CSS with the Web Animations API. FFmpeg encodes the frames.

*Benefit:* one surface, one visual language, and **Playwright is already
installed and working in this repo** — it is what the web app's browser tests
run on. No new rendering dependency at all.
*Cost:* Motion Canvas's animation ergonomics (tweening, signals, scene
timing) have to be hand-rolled.

### Option C — Motion Canvas for video, Monaco for the app *(recommended)*
Video code scenes use Motion Canvas's built-in `Code` node, which already does
syntax highlighting and typing/diff animation. Monaco is used where it is
genuinely better — the **interactive practice surface in the web app**, where
a learner types real Python and runs it.

*Benefit:* one video pipeline, one visual language, and Monaco still ships in
the product doing the thing DOM editors are actually good at: being edited.
*Cost:* video code panes are styled by Motion Canvas, not by Monaco, so the
editor in a Meal and the editor in the practice panel need deliberate visual
matching.

**Recommendation: C.** It honours the §10 requirement (typing animation,
cursor, line highlighting, error states — all supported by the Code node),
keeps Monaco in the product, and avoids compositing two render surfaces before
the pipeline has ever produced a single Meal.

This decision does **not** block schema design. The Meal JSON is
renderer-agnostic by requirement (§17, §32), so the schema below is valid
under all three options.

---

## 3. Reuse audit — what carries into Meals

Evaluated individually, per §26, rather than assumed correct.

### Reuse as-is

| Asset | Why it carries over |
|---|---|
| `podcastengine._tts_with_retry` | edge-tts with exponential backoff and an empty-file guard. Free, local, already hardened against the transients that break naive TTS. |
| `explainchat.render_chat_audio` | Sentence chunking, bounded-concurrency synthesis, pydub stitching, temp cleanup. This is the voice track for a Meal, essentially unchanged. |
| `reel_planner._time_from_whisper` | **The most valuable single piece.** Transcribes our *own* generated audio with `word_timestamps=True`, aligns by positional walk, and bails to an estimator if word-count drift exceeds 20%. That is exactly the caption/animation sync a Meal needs. |
| The two-stage plan/render split | `plan_reel()` decides editorially, then render executes. This is precisely the §9 architecture. Reused as a *pattern*. |
| FFmpeg + MoviePy compositing at 1080x1920/30fps | Already producing correct 9:16 output. |
| `POST /chat/execute-code` | Real execution for the EXECUTION beat — with the caveat in §5 below. |
| Transcription and ingest (`chipper.transcribe`, YouTube path) | Needed unchanged for the long-form-to-Meals path (§18). |
| Auth, Supabase, upload, media serving | Untouched. |
| Design tokens shared with `frontend/style.css` | The Meal visual identity should inherit them. |

### Reuse with modification

| Asset | Change needed |
|---|---|
| `reel_planner.plan_reel()` | Its beat structure is `hook -> concept -> analogy -> PYQ tie-in`. Meals need eight beats. **New planner module alongside it**, not an edit. |
| `reel_planner.compose_reel()` | Composites captions over a solid background. Meals need scene-based visuals. New renderer; the caption layer logic is a useful reference. |

### Do not reuse for Meals

| Asset | Why |
|---|---|
| `chipper.cut_clips()` as a Meal source | It ffmpeg-cuts the original video. §19 is explicit: a source clip is *evidence*, not Meal content. A Meal is newly rendered. The clip endpoint stays for the lecture pages. |
| `chipper._segment_windowed()` as Meal boundaries | 20-minute sliding windows, `MAX_MODULES=8`, max 4 per window — **duration-driven**. §18 rules this out. Meal boundaries come from learning objectives. Chipper still provides timestamps and source evidence. |
| `prep_mode.CONCEPTS` | 12 hardcoded Operating Systems ids; the tagger has no "none" escape and defaults to `processes`. Unusable for Python. A Python taxonomy is needed (§20). |

---

## 4. Where the new code lives

Nothing is added to `main.py` or any existing backend module.

```
MAROS/
  meals/                      NEW - Python. Schema + planner + Meal JSON.
    schema/meal.schema.json   the canonical contract
    catalogue/*.json          hand-authored Meals (the first three)
  meal-renderer/              NEW - Node. Consumes Meal JSON, emits MP4.
  web/                        the web app; gains the Meal feed
  main.py, chipper.py, ...    UNCHANGED
```

The renderer is a separate Node project because the rendering stack is
JavaScript. It is a **consumer of Meal JSON**, not a service the backend
depends on — so the Python backend keeps its position as source of truth.

---

## 5. Code execution — evaluate before depending on it (§14)

`POST /chat/execute-code` proxies to **glot.io**, a third-party service, and
requires `GLOT_API_TOKEN`.

Measured state on this machine: the token is configured, but `run.glot.io`
does not resolve, so the endpoint returns **502**. It is currently unusable
here.

Putting a third-party HTTP service on the critical path of *every programming
Meal render* is a real risk: if glot.io is down or rate-limits, Meal
generation stalls.

The governing rule from §14 is absolute and is respected in the schema below:

> **NEVER CLAIM THAT CODE EXECUTED IF IT DID NOT ACTUALLY EXECUTE.**

The schema therefore stores terminal output as a **recorded result with
provenance**, not as authored text — see `execution.verified` and
`execution.source`. A Meal whose code has not actually run is marked as such
and must not be published.

Local sandboxed CPython is the obvious longer-term replacement for a Python-
first product, and would remove the dependency entirely. Not built now;
recorded as the recommended direction.

---

## 6. Renderer decision (confirmed) and what it cost

**Option C was chosen:** Motion Canvas renders the video, including code scenes
via its built-in `Code` node; Monaco is reserved for the web app's interactive
practice panel. One video pipeline, one visual language, both MIT.

### Pipeline as built

```
meals/catalogue/*.json          the Meal (content, renderer-agnostic)
        |
        +-- meals/validate.py   schema + semantic checks
        +-- meals/verify.py     ACTUALLY runs the code, records real output
        |
meals/narrate.py                edge-tts -> mp3, Whisper forced alignment
        |                       -> meals/build/{id}.timing.json
        v
meal-renderer/render.mjs        Vite + headless Chromium
        |                         - Motion Canvas Renderer + custom Exporter
        |                         - frames streamed out as PNG
        v
FFmpeg                          PNG sequence + mp3 -> 1080x1920 30fps MP4
        |
        v
meals/out/{id}.mp4
```

Measured on Meal 1: 37.0s of narration, 1111 frames, rendered in ~8.5s.

### Two things worth knowing before extending the renderer

**1. Motion Canvas 3.17 has no headless render CLI.** The `@motion-canvas/ffmpeg`
exporter runs inside the editor UI. Driving that UI with a browser robot would
be fragile — it renders whatever the editor happens to be showing. Instead the
driver uses the same public API the editor itself uses: `Renderer` plus a
custom `Exporter` whose `handleFrame` streams PNGs out to Node. Two details
that were not obvious:

- `@motion-canvas/vite-plugin` is CJS with no `exports` map, so under ESM the
  callable is at `.default.default`.
- Importing the project with the plugin's own `?project` suffix yields a
  bootstrapped `Project` (logger, meta files). Importing `makeProject`'s
  result directly gives bare settings and `Renderer` fails on `project.logger`.
- The plugin serves its editor at `/`, so the headless page needs its own URL.
- Vite's dep cache must be busted per run (`optimizeDeps.force`). Without it an
  edited scene silently renders from a stale module — which is very hard to
  spot when the thing you are inspecting is a video.

**2. Motion Canvas does not lay out sibling `Txt` nodes.** Several `Txt`
children of one container all paint at the container origin and pile up. This
was tested three ways — `layout` on the parent, `layout` on each child, and a
`Rect` wrapper per word — and all three rendered identically broken.

The consequence is that **per-word colour inside a statement is not available**.
A single `Txt` with `textWrap` wraps correctly (it is what the caption track
uses), so statements are one `Txt` in one colour, and emphasis is carried by
the beat's eyebrow instead — a green "TAKEAWAY" above the line. It reads as
deliberate rather than as a missing feature.

The `emphasis` field stays in the Meal schema. It is renderer-agnostic content,
so a future renderer can honour it without a schema change.

### The execution guard rail is enforced twice

`verify.py` runs the code for real and stamps `verified: true` with a
timestamp and the executor name. `validate.py` refuses any Meal whose terminal
scene is unverified, and the renderer **throws** rather than draw an
unverified terminal. Meal 1's output is a genuine capture:

```json
{ "verified": true, "source": "local_sandbox",
  "stdout": "What's your name? Hello Bir\n", "exit_code": 0 }
```

Local CPython was used rather than glot.io, which is unreachable from this
machine and is a third-party service that should not sit on the critical path
of every render.
