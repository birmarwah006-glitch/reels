# MAROS — Frontend Plan

**Status: approved, in implementation.**

Read `current-architecture.md` first — it establishes that the backend lives in
`~/Desktop/MAROS`, not in this repository, and that it is a FastAPI + vanilla-JS
stack with no Node toolchain anywhere.

---

## 1. Approved decisions

### D1 — Frontend location: `MAROS/web/` (APPROVED)

The new product UI is built at `~/Desktop/MAROS/web/`.

`MAROS/frontend/` and its `/app` and `/static` mounts are **left untouched**.
The existing vanilla-JS app keeps working throughout. The Python backend is
not modified, migrated, or rewritten.

Documentation stays canonical in `~/Desktop/reel/docs/` (this directory), which
is where it is reviewed; `MAROS/web/README.md` points back here rather than
duplicating it.

### D2 — Lecture-first V1 (APPROVED)

Every page is wired to a real, existing endpoint. `/concepts` is **not mocked**
and no API response is invented. Where a required endpoint does not exist, the
feature is either built against what genuinely exists or documented in
`frontend-api-gaps.md` and left unbuilt.

### D3 — Web app first; visual engine deferred (APPROVED)

Build and validate the full learning experience before any video engine:

```
Explore -> concept discovery -> add lecture -> processing -> extracted concepts
       -> learning path -> concept lesson -> code example -> quiz
       -> Ask MAROS -> progress
```

Motion Canvas, Monaco, and FFmpeg are **not installed and not implemented** at
this stage. Monaco is deferred even in the web app: the lesson page uses a
lightweight highlighted code block until a real editing requirement appears.

---

## 2. Verified against the running backend

The backend was started locally (`uvicorn main:app`, port 8000) and every
endpoint below was probed before any UI was written. These are measured facts,
not assumptions.

| Probe | Result |
|---|---|
| `GET /` | `{"system":"MAROS","status":"online","version":"1.0.0"}` |
| `GET /lectures` | **exactly 1 lecture.** 13 dirs in `outputs/`, only 1 has a `manifest.json` |
| The one lecture | `fcece514...`, source `youtube`, 6 modules, "Lecture 1: Predicates, Sets, and Proofs" |
| `GET /modules/{job}/{m}/notes` | **2 of 6 modules carry the notes-failure sentinel** (modules 2 and 4) |
| Mermaid in notes | present in 3 of 6 modules |
| `GET /modules/{job}/{m}/video` | 206 Partial Content, `video/mp4` — range requests work, so seeking works |
| `GET /reels/{job}` | 1 reel exists (module 4), `reel_status: done`, 29.9s, **word-level caption timings** |
| `GET /clipper/reels/{job}` | `[]` — clipper media lives in the *other* repo's `output/` |
| `POST /quiz/generate` | works, ~2.5s, returns real MCQs |
| `POST /chat` | works; **returns LaTeX** as `\(...\)`, so math rendering is required |
| `GET /student/*` logged out | `{[], message: "Login to ..."}` — 200, not 401, as documented |
| `GET /jobs/{job}/youtube` | returns `video_id`, so a YouTube embed is possible |
| `POST /chat/execute-code` | **502** — token is configured, but `run.glot.io` does not resolve from this machine |

### Consequences the UI must handle

1. **Explore has one lecture.** The empty and near-empty states are the common case today, not an edge case. They get designed properly rather than as an afterthought.
2. **Notes fail per module.** `chipper._NOTES_FAILED_SENTINEL` appears in real data. The lesson page detects it and shows an honest "notes need regenerating" state alongside the concept map, which is often still fine.
3. **Math and Mermaid are both required** in notes and in chat.
4. **Code execution can be down while configured.** The 502 body carries the real reason; it is surfaced rather than flattened to "something went wrong".

### CORS: solved without touching the backend

`.env` sets `CORS_ORIGINS=http://localhost:8000`, so a Vite dev server on
`:5173` is **not** an allowed origin. Rather than edit backend config, the dev
server **proxies** the API path prefixes to `127.0.0.1:8000`. The browser only
ever talks to its own origin, so CORS never applies. In production FastAPI
serves the built assets same-origin, so the identical relative paths work.

This is why the API client uses **relative paths with no base URL**.

---

## 2. Stack

There is no existing frontend framework to continue — `frontend/` is hand-written
vanilla JS with no build step and no `package.json` in either repo. So "use the
existing stack" resolves to a greenfield choice. Keeping it conventional:

| Layer | Choice | Why |
|---|---|---|
| Framework | **React 18 + TypeScript** | The brief assumes React. Types matter given how loose the API shapes are. |
| Build | **Vite** | Fast, no config, builds to static files FastAPI can serve. |
| Routing | **React Router** | |
| Styling | **Tailwind CSS** | The brief names it. Tokens come from `frontend/style.css`, not Tailwind defaults. |
| Primitives | **shadcn/ui**, heavily restyled | Copy-in, not a dependency, so restyling is real rather than override-fighting. |
| Server state | **TanStack Query** | Polling with backoff is exactly what the job/reel status endpoints need. |
| Auth | **`@supabase/supabase-js`** | Must produce the same `localStorage["maros_session"]` shape the backend's existing clients use. |
| Markdown | **react-markdown + remark-gfm** | Module notes are markdown with an inline Mermaid block. |
| Math | **KaTeX** | `/chat` and quiz explanations return LaTeX. Verified against the live API. |
| Diagrams | **mermaid** | Notes carry linted `flowchart` blocks. |
| Code | **Shiki or Prism**, not Monaco | Nothing in V1 needs a full editor. Monaco is deferred to the visual engine per D3. |

Nothing here touches Python. No backend file is modified in the frontend build.

---

## 3. Design system

Derived from `frontend/style.css` and the tokens already hardcoded in
`reel_planner.py`, so the web app, the existing app, and rendered reels agree.

```
surface   #0a0a0a      accent   #c8f060  (MAROS green)
text      #f0ede6      muted    #555550
```

Dark-first. The accent is a signal colour — progress, current node, active
state, primary CTA — never decoration.

Direction, against the brief's exclusions: technical and editorial. Real
typographic hierarchy instead of giant text everywhere. Structure carried by
spacing and rules, not by gradients, glows, floating blobs, or ambient motion.
Animation only where it explains something — a pointer advancing, a line
highlighting, a bar filling.

**Components to build** (the brief's list, grouped by when they are first needed):

- *Foundation*: typography scale, spacing scale, Button, Input, Card, Badge, Nav, Modal
- *Content*: CodeBlock, Terminal, ConceptCard, VideoCard, ProgressBar, StepPath
- *States*: Loading (skeletons, not spinners), Empty, Error

Built as they are needed, not as an upfront component library with no consumers.

---

## 4. Pages, and what each is actually wired to

`OK` = real endpoint exists. `GAP` = see `frontend-api-gaps.md`.

| # | Page | Endpoints | Status |
|---|---|---|---|
| 1 | Landing | static; live sample from `GET /lectures` | OK |
| 2 | Explore / feed | `GET /lectures`, `GET /modules/{job_id}`, `GET /clipper/reels/{job_id}`, `GET /reels/{job_id}` | OK, **lecture-first** (GAP 1, 2) |
| 3 | Concept lesson | `GET /modules/{id}/{mid}/notes`, `.../video`, `POST /quiz/generate`, `POST /quiz/submit`, `POST /chat/execute-code` | OK for lecture modules; GAP 3 for catalogue concepts |
| 4 | Add lecture | `POST /jobs` (mp4/mov/avi/mp3/wav), `POST /jobs/youtube` | OK. **No "course URL" input** — the backend does not support it. |
| 5 | Processing | `GET /jobs/{id}` -> on 404 `GET /jobs/{id}/manifest` -> `GET /lectures` | OK via fallback chain (GAP 7) |
| 6 | Concept map | `GET /modules/{job_id}` | Time-ordered path only; no prerequisite edges or locks (GAP 5) |
| 7 | My Learning | `GET /student/mastery`, `GET /student/classwork`, `GET /student/next` | Per-concept only; no course roll-up (GAP 6) |
| 8 | Profile | Supabase session, `GET /student/mastery` | OK, thin |
| 9 | Ask MAROS | `POST /chat`, `POST /chat/upload`, `GET/DELETE /chat/history` | OK; no `concept_id` context (GAP 4) |

Not building, per the brief: DMs, followers, threaded comments, creator
marketplace. The one exception is the flat comment list on clipper reels, which
already exists at `/clipper/reels/{job_id}/{module_id}/comments` and is
read-mostly.

### Add-lecture inputs, precisely

The brief lists three. The backend supports two:

- **Upload video** — `POST /jobs`, multipart. `video/mp4`, `video/quicktime`, `video/x-msvideo`, `audio/mpeg`, `audio/wav`, `audio/x-wav`. Anything else 400s.
- **YouTube URL** — `POST /jobs/youtube`, `{url}`. Audio only; playback is a YouTube embed. Depends on `yt-dlp` + Deno + a cookies file, and fails in ways worth surfacing honestly.
- **Course URL** — **no endpoint. Will not be built.** Logged as a gap.

---

## 5. API client conventions

One typed client under `web/src/api/`, mirroring `frontend/api.js`'s auth so both
frontends stay compatible:

- **Base URL: none — all paths are relative.** The dev server proxies API prefixes to `127.0.0.1:8000`; production serves same-origin from FastAPI. This sidesteps `CORS_ORIGINS=http://localhost:8000` without editing backend config.
- `Authorization: Bearer <token>` from `localStorage["maros_session"].access_token`.
- `X-Prof-Token` from `sessionStorage["maros_prof_token"]` when present.

Three backend behaviours the client must handle rather than treat as errors:

1. **202 is not failure.** `GET /jobs/{id}/manifest` returns 202 while processing.
2. **Logged-out is not 401.** The `/student/*` endpoints return `{[], message: "Login to ..."}`. Render the message, do not redirect.
3. **Code execution has two distinct failure modes, both observed.** `/chat/execute-code` returns **503** when `GLOT_API_TOKEN` is unset ("not configured") and **502** when glot.io is unreachable ("configured but unreachable" — the live state on this machine). Both carry a real reason in `detail`; show it.
4. **Notes can be a failure sentinel with a 200 body.** A module's notes may be `[Notes generation failed — this module needs to be regenerated]`. Detect it; do not render it as lesson content.

Every view gets explicit loading, empty, and error states. Given the backend's
documented flakiness — reel script quality varying run to run, `gpt-oss-120b`
returning empty on long prompts — retry affordances are a product requirement,
not polish.

---

## 6. Visual engine (DEFERRED per D3 — spec only, nothing built)

**Nothing in this section is implemented.** Motion Canvas, Monaco and FFmpeg
are not installed. This records the agreed shape so the web app and the
backend do not paint it into a corner.

### The video beat structure (specified by the product owner)

```
HOOK
  ↓
QUESTION / PROBLEM
  ↓
CONCEPT
  ↓
VISUAL EXPLANATION
  ↓
CODE / EXAMPLE
  ↓
EXECUTION
  ↓
TAKEAWAY
  ↓
PRACTICE
```

Eight beats, fixed order. This is the authoritative structure for a MAROS
educational video.

### How it differs from the earlier lesson-page brief

The original Concept Learning Page listing had seven beats and no explicit
problem statement. Two changes matter:

1. **QUESTION / PROBLEM is new, and it sits second.** The video states the
   problem *before* it explains the concept. A hook grabs attention; the
   question is what makes the explanation feel necessary. Nothing in the
   current pipeline produces this.
2. **"Ask MAROS" is not a video beat.** It is interactive, so it belongs to
   the lesson page only. PRACTICE closes the video instead.

### Conflict with the existing reel planner — must be resolved before building

`reel_planner.plan_reel()` already emits a `beats` field, but to a *different*
structure:

```
hook -> core concept -> analogy -> PYQ tie-in + common mistake
```

That is four beats, it treats the past-year question as a closing tie-in
rather than an opening problem, and it has no visual, code, or execution beat
at all. The two structures are not compatible.

**Recommendation:** the new eight-beat planner is a *separate* module
alongside `reel_planner.py`, not an edit to it. `reel_planner` is working,
serves `/reels/*`, and has a rendered artefact on disk today. Per the standing
rule that working backend code is not rewritten, it stays as-is and the new
engine is additive — a third renderer, as already noted in
`current-architecture.md`.

### What each beat needs, and whether it exists today

| Beat | Source available now | Status |
|---|---|---|
| HOOK | `plan_reel()` writes `hook_angle` | exists, reusable |
| QUESTION / PROBLEM | nothing produces this | **missing** |
| CONCEPT | `plan_reel()` `concept` + `script` | exists, reusable |
| VISUAL EXPLANATION | nothing — no component/timeline schema anywhere | **missing (GAP 9)** |
| CODE / EXAMPLE | nothing generates code from a lecture | **missing** |
| EXECUTION | `POST /chat/execute-code` returns real stdout/stderr | exists |
| TAKEAWAY | `plan_reel()` `common_mistake` + script close | partial |
| PRACTICE | `pyq_pool.json`, 93 tagged questions | exists, **Operating Systems only** |

Three of eight beats have no producer. The visual beat is the largest: it
needs the `{components[], timeline[]}` instruction schema, which should be
defined by a working renderer before any LLM is prompted to emit it.

### One structure, two consumers

The eight beats describe a video, but they are also very close to what the
lesson page already renders. If a single lesson JSON carries all eight, the
web page and the renderer can consume the same document rather than diverging
into two content models. Worth designing for from the start, since retrofitting
it later means reworking both.

**Note on EXECUTION:** because `/chat/execute-code` is real, the terminal beat
can show genuine program output rather than pre-written text. A video that
prints what the code actually printed is worth the wiring.

## 7. Proposed order

Each phase ends with something runnable in a browser.

| Phase | Work | Exit condition | Status |
|---|---|---|---|
| 0 | Scaffold at `MAROS/web/`, Tailwind tokens from `style.css`, typed API client, auth wiring | `GET /lectures` renders real data | **done** |
| 1 | Design system foundation + Landing | Landing page ships, responsive | **done** |
| 2 | Explore (lecture-first) + reel playback | Browse real processed lectures and play real reels | **done** |
| 3 | Lesson page: notes + Mermaid, video, quiz, code execution | A real module is learnable end to end | **done** |
| 4 | Add lecture + processing, with the fallback chain | Upload a video, watch it process, land on the result | **done** |
| 5 | Concept map (time-ordered) + My Learning | Progress visible against real mastery data | **done** |
| 6 | Ask MAROS + Profile | Tutor reachable in context | **done** |
| 7 | Mobile pass: feed on phone, workspace on desktop | Tested at all three widths | **done** |
| 8 | *(deferred)* Visual engine | Out of scope per D3 | deferred |

Phases 0 through 7 are the approved scope. Phase 8 is explicitly deferred.

### Verification standard

Every phase was checked in a real browser, not assumed. `npm run smoke` loads
each route at 390 / 820 / 1440 px and fails on console errors, page errors,
failed requests, or horizontal overflow. `npm run flow` drives the real quiz
and tutor against the live backend. Both are committed alongside the app.

Bugs this actually caught, rather than theory:

- the landing hero overflowed horizontally by 75 px on a phone and 39 px on a
  tablet (grid children missing `min-w-0`, plus an oversized headline)
- `text-wrap: balance` fighting authored `<br>` breaks into four ragged lines
- chipper writes the concept title twice at the top of every notes file, so
  the lesson page showed it three times
- GitHub alert syntax (`> [!NOTE]`) rendering its marker as literal text,
  because remark-gfm does not implement alerts
- the job-status fallback chain being wrong — see the correction in
  `frontend-api-gaps.md` GAP 7
- a 1.4 MB main bundle, because mermaid was imported at module scope

---

## 8. Risks

1. **Concept-vs-lecture mismatch.** The deepest risk and the reason for D2. The brief's product is concept-first; the backend is lecture-first. Building concept UI over lecture data means either shipping something narrower or building backend first. Choose deliberately.
2. **The OS-only taxonomy with no "none" escape.** Documented in `reel`'s README: the tagger hard-defaults to `processes`. A Python lecture uploaded today gets tagged as an OS concept and pulls irrelevant exam questions. Any non-OS subject in the UI will surface this immediately.
3. **In-memory job store.** Single-process only. Restarts lose jobs. This constrains deployment, not just the UI, and the fallback chain hides it rather than fixing it.
4. **The clipper fork.** Two diverged copies across two repos with no sync. Whoever edits the wrong one loses work. Worth resolving before it grows.
5. **Uncommitted work.** 138 lines in `reel/clipper/generate_script.py` right now. Commit before anything else lands.
6. **`main.py` at 2262 lines.** Every gap in `frontend-api-gaps.md` implies more routes. They should go in new routers following the existing five-router pattern, not into `main.py`.
7. **LLM flakiness is user-visible.** Empty completions on long prompts and run-to-run script variance mean regenerate buttons and honest error copy are load-bearing.
8. **Production config.** `CORS_ORIGINS` defaults to `*` with `allow_credentials=True`. `config.py` raises at import without `GROQ_API_KEY`. Machine-specific absolute paths are committed. All will bite on first deploy of a separately-hosted frontend.
9. **Two frontends during transition.** `frontend/` stays live at `/app` until the new app reaches parity. Auth token shapes must match exactly or users get logged out crossing between them.
