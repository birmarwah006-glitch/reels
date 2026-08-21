# MAROS — Current Architecture

Written after a read-only inspection. Nothing was modified.

---

## 0. The most important finding: there are two repositories

The brief assumed one repo. There are two, and the backend is **not** in the
working directory.

| | Path | What it is | Git |
|---|---|---|---|
| `reel` | `~/Desktop/reel` | The `clipper` pipeline only. Two-character comedic explainer reels. No web server, no API, no frontend. | own repo, 1 commit |
| `MAROS` | `~/Desktop/MAROS` | The actual product. FastAPI backend (~10k lines Python), vanilla-JS frontend, Supabase, all media pipelines. | own repo, `MAROS-BM` on GitHub |

`reel` depends on `MAROS` at runtime: `clipper` imports `chipper.py`'s LLM
router and reads lecture manifests out of `MAROS/outputs/`. The path comes from
`MAROS_ROOT`, defaulting to `~/Desktop/MAROS`. The `reel` README states
**"MAROS is read-only: nothing here writes to that tree."**

`MAROS/clipper/` is a **second, diverged copy** of the same package. See
Technical Debt below.

**The "60% complete backend" is `~/Desktop/MAROS`.** All statements below
describe that repo unless stated otherwise.

---

## 1. Tech stack as it actually exists

### Backend
- **Framework**: FastAPI (0.136.1), served by uvicorn. Entry point `main.py` (2262 lines).
- **Language**: Python 3, Pydantic v2 models.
- **Routers**: five `APIRouter`s included into `app` in `main.py` lines 122-141.
- **Async model**: `BackgroundTasks` for every long job (transcription, podcast, reel). No Celery, no queue, no worker process.
- **Job store**: `jobs.py` — a plain in-memory `dict`. **Job status does not survive a server restart.** `GET /lectures` compensates by reading manifests off disk instead.
- **Media**: MoviePy 2.x + ffmpeg, faster-whisper (local ASR), edge-tts / gTTS.

### Database
- **Supabase** (Postgres), accessed with the `service_role` key from `supabase_layer.py`, so RLS is bypassed server-side.
- Everything Supabase-backed **degrades gracefully to disabled** if `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` are absent — `get_sb()` returns `None` and callers no-op.
- Known tables: `interaction_log`, `quiz_answers`, `oak_chats`, `reel_comments`, `students`.
- Known RPCs: `student_mastery_summary`, `update_mastery`, `ready_to_learn`.
- **There is no committed migration/schema for most of this.** The only SQL in either repo is `MAROS/clipper/schema.sql`, which covers `reel_comments` alone.

### Vector store
- **ChromaDB** at `MAROS/chroma_db/`, wrapped by `rag.py` (`build_rag_context`, `get_rag_stats`). Corpus is OS exam papers, ingested by `ingest_papers.py` / `RAG-MAROS/rag_ingestion.py`.

### Auth
- **Supabase JWT**, passed as `Authorization: Bearer <access_token>`.
- `get_current_user(request)` returns a user id **or `None`** — it never raises. Most endpoints are written to work logged-out with degraded data.
- `require_user(request)` is the strict variant (401). Only Prep Mode routes use it.
- **Professor auth is a separate shared secret**, not JWT: header `X-Prof-Token` compared against the `PROF_SECRET` env var by `require_professor()`.
- The existing frontend stores the session at `localStorage["maros_session"]` and the prof token at `sessionStorage["maros_prof_token"]` (`frontend/api.js` lines 10-17).

### Frontend (existing)
- **Vanilla HTML/CSS/JS. No build step, no npm, no `package.json` anywhere in either repo. No React, no Tailwind, no TypeScript.**
- ES modules loaded straight from the page; `frontend/api.js` is the single API client.
- Four hand-written pages, all large single files:
  - `index.html` (11 KB) — entry/login
  - `student.html` (83 KB)
  - `professor.html` (59 KB)
  - `prepmode.html` (32 KB)
  - `notes-renderer.js` (43 KB) — markdown + Mermaid + math renderer for module notes
  - `style.css` (16 KB) — the de-facto design tokens
- Served by FastAPI itself at the end of `main.py`:
  `app.mount("/app", StaticFiles(directory="frontend", html=True))` and `/static`.
- `maros frontend/index.html` is an empty stub. Ignore it.

### External services
| Service | Used for | Env var | Failure mode |
|---|---|---|---|
| Groq | Primary LLM (`openai/gpt-oss-120b`) + `whisper-large-v3` | `GROQ_API_KEY` | **hard `RuntimeError` at import of `config.py`** |
| Cerebras | Preferred LLM when funded; currently commented out (account 402) | `CEREBRAS_API_KEY` | falls back to Groq |
| Supabase | Auth, mastery, logging | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | warns, disables features |
| glot.io | Sandboxed code execution | `GLOT_API_TOKEN` | 503 from `/chat/execute-code` |
| yt-dlp + Deno | YouTube ingest | `YTDLP_COOKIES_FILE`, `DENO_PATH` | download fails |
| DuckDuckGo (`ddgs`) | Oak web search | none | returns "" |

Other env vars: `PROF_SECRET`, `CORS_ORIGINS` (default `*`), `HOST`, `PORT` (8000),
`WHISPER_MODEL`, `MAX_MODULES` (8), `MIN_CLIP_DURATION`, `TRANSCRIPT_CAP`.

---

## 2. The processing pipeline (chipper.py)

This is the core of the product and the thing the frontend's "add lecture" flow drives.

```
upload mp4/mov/avi/mp3/wav   OR   paste a YouTube URL
        |
        v
transcribe()            faster-whisper, local, word-level segments
        |
        v
segment()               LLM over 20-min sliding windows -> module boundaries
                        (<= 4 modules/window, MAX_MODULES cap, MIN_CLIP_DURATION floor)
        |
        v
summarize()             per module: detailed markdown notes + a summary
generate_concept_map()  per module: a Mermaid diagram, LINTED before acceptance
        |                (spliced INLINE into the notes at the %%CONCEPT_MAP%% token)
        v
cut_clips()             ffmpeg cuts Module_NN_<slug>.mp4 + _notes.txt
        |
        v
outputs/{job_id}/manifest.json
```

`JobStatus` progresses `queued -> transcribing -> segmenting -> cutting -> summarizing -> done | failed`, with an integer `progress` 0-100.

### On-disk layout (the real data model)
```
MAROS/
  uploads/{job_id}.{ext}                       raw source
  outputs/{job_id}/manifest.json               THE lecture record
  outputs/{job_id}/Module_NN_<slug>.mp4        cut clip
  outputs/{job_id}/Module_NN_<slug>_notes.txt  markdown notes + inline Mermaid
  outputs/{job_id}/youtube.json                present only for YouTube jobs
  outputs/{job_id}/reel_NN.json                reel_planner sidecar
  outputs/{job_id}/quiz_modNN.json             professor-published module quiz
  outputs/_papers/{paper_id}/{paper.pdf,meta.json,explain_chat.json}
  outputs/_assignments/...
  outputs/_prof_quizzes/{quiz_id}/quiz.json
  output/{job_id}_reel_NN.{mp3,mp4}            reel media (note: `output`, not `outputs`)
  clipper/output/{job_id}_reel_{n}.mp4         clipper media
```

Note the two sibling directories `outputs/` (structured job data) and `output/`
(generated media). This is not a typo in this document; both exist and are used.

---

## 3. Data models

From `models.py` plus the JSON shapes on disk.

```python
Job       { job_id: str, status: JobStatus, progress: int, error: str|None, created_at: datetime }
JobStatus = queued | transcribing | segmenting | cutting | summarizing | done | failed

Module    { module_id: int, concept: str, start: "MM:SS", end: "MM:SS",
            duration_sec: float, video_url: str, notes: str, transcript: str }

Manifest  { job_id: str, video_source: str, total_modules: int,
            modules: Module[], generated_at: datetime }

QuizQuestion { question: str, options: {A,B,C,D}, correct_answer: str,
               explanation: str, module_id: int }
Quiz         { quiz_id, module_id, topic, questions: QuizQuestion[], generated_at }

ChatMessage  { role: str, content: str, module_id: int|None, timestamp: datetime|None }
ChatRequest  { message, job_id?, module_id?, paper_id?, history: ChatMessage[],
               role: "student"|"professor", mode: "videos"|"papers"|"assignments"|"prep" }
```

Real values are coarser than the brief assumes. A `concept` from a real manifest:
`"Course logistics, assessment policies, and collaboration guidelines"` —
these are **lecture segments**, not atomic teachable concepts like "Python for loops".

### The concept taxonomy
`prep_mode.CONCEPTS` is a hand-written dict of **12 concepts, Operating Systems only**:

```
computer-architecture, cpu-virtualization, processes, scheduling,
memory-virtualization, paging, concurrency, threads, locks,
persistence, ...  (12 total)
```

Every downstream feature — PYQ matching, mastery scoring, reel planning, prep
mode — is keyed on these 12 ids. There is **no taxonomy for Python, C/C++, Data
Structures, Algorithms, or AI/ML**.

### Question bank
- `questions.csv` (238 KB, 567 questions), `extract_cache.json`, `tag_cache.json`, `type_cache.json`
- `reel/clipper/data/pyq_pool.json` — 93 concept-tagged past-year questions
- All Operating Systems, VNIT Nagpur.

---

## 4. Available APIs

Base URL `http://localhost:8000` in dev, same origin in prod.
`+` = reads `Authorization: Bearer` if present but works without.
`!` = hard-requires auth. `P` = requires `X-Prof-Token`.

### Jobs and lectures — `main.py`
| Method | Path | Notes |
|---|---|---|
| POST | `/jobs` | multipart `file`. mp4/mov/avi/mp3/wav only. Returns `Job`, processes in background. |
| POST | `/jobs/youtube` | `{url}` -> `Job`. Audio only; video is watched via YouTube embed. |
| GET | `/jobs/{job_id}` | `Job`. **In-memory — 404s after a restart.** |
| GET | `/jobs/{job_id}/manifest` | `Manifest`. 202 while still processing. |
| GET | `/jobs/{job_id}/youtube` | `{source:"upload"}` or the YouTube metadata. |
| GET | `/lectures` | Array of `{job_id, title, total_modules, generated_at, source, modules:[{module_id, concept}]}`. Disk-backed, restart-safe. **This is the real lecture list.** |
| GET | `/modules/{job_id}` | `Module[]` |
| GET | `/modules/{job_id}/{module_id}/video` | mp4 FileResponse |
| GET | `/modules/{job_id}/{module_id}/notes` | `{module_id, notes}` markdown with inline Mermaid |

### Quiz
| Method | Path | Notes |
|---|---|---|
| POST | `/quiz/generate` | `{job_id, module_id, num_questions=5}` -> `Quiz`. Serves a professor-published quiz if one exists, else generates. |
| POST + | `/quiz/submit` | `{job_id, module_id, answers[]}` -> `{total, correct, score, misconceptions[]}`. Runs LLM misconception diagnosis and updates mastery. |

### Chat / Oak tutor
| Method | Path | Notes |
|---|---|---|
| POST + | `/chat` | `ChatRequest` -> `ChatMessage`. RAG-grounded in `videos` mode. Detects struggle cues, decays mastery, persists the turn. |
| POST | `/chat/execute-code` | `{language, code}` -> `{stdout, stderr, exit_code}` via glot.io |
| POST + | `/chat/upload` | PDF/image -> extracted text held in a **process-local in-memory dict** |
| GET + | `/chat/history` | persisted threads (`oak_chats`) |
| DELETE + | `/chat/history` | |

### Student
| Method | Path | Notes |
|---|---|---|
| GET + | `/student/mastery` | `{student_id, mastery: [{concept_name, score, ...}]}` |
| GET + | `/student/classwork` | quiz attempts grouped into sessions |
| GET + | `/student/next` | `{next_concepts}` from the `ready_to_learn` RPC |

Each returns `{[], message: "Login to ..."}` when logged out rather than 401.

### Reels — `reel_routes.py`, prefix `/reels`
| Method | Path | Notes |
|---|---|---|
| POST | `/reels/{job_id}/{module_id}` | `?exam_type=midsem\|endsem&force=` — kicks off, returns immediately |
| GET | `/reels/{job_id}` | every sidecar for the job |
| GET | `/reels/{job_id}/{module_id}` | plan + script + captions + `reel_status` (`not_started`/`generating`/`done`/`failed`) |
| GET | `/reels/{job_id}/{module_id}/video` | 9:16 mp4 |
| GET | `/reels/{job_id}/{module_id}/audio` | narration mp3 |

### Clipper reels — `clipper_routes.py`, prefix `/clipper/reels`
| Method | Path | Notes |
|---|---|---|
| GET | `/clipper/reels/{job_id}` | `[{module_id, concept, video_url, thumbnail_url: null}]` |
| GET | `/clipper/reels/{job_id}/{module_id}/video` | mp4 |
| GET | `/clipper/reels/{job_id}/{module_id}/comments` | `[]` if Supabase is off |
| POST + | `/clipper/reels/{job_id}/{module_id}/comments` | `{text}` |

Read-only. **There is no endpoint that generates a clipper reel** — that is CLI-only.

### Prep mode — `prep_routes.py`, prefix `/prep`. All `!` (401 without login)
`POST /prep/start`, `POST /prep/plan`, `GET /prep/session`, `GET /prep/tree`,
`POST /prep/jump`, `GET /prep/paper`, `POST /prep/stage`, `GET /prep/important-questions`.

`GET /prep/tree` returns every concept with a status — the closest thing that
exists to a learning-path graph, but it is the fixed 12-concept OS syllabus,
not a per-lecture map.

### Papers, podcast, explain
`POST/GET /papers`, `GET/PATCH/DELETE /papers/{id}`, `POST/GET /papers/{id}/podcast`,
`GET /papers/{id}/podcast/audio`, and `POST/GET/DELETE /papers/{id}/explain/chat`
plus `GET /papers/{id}/explain/chat/audio/{seq}`.

### Assignments
`POST/GET /assignments`, `PATCH /assignments/{aid}/visibility`,
`GET /assignments/{aid}/file`, `POST /assignments/{aid}/submit`,
`GET /assignments/{aid}/submissions[/{filename}]`.

### Professor — `professor_tools.py`, all `P`
`POST /professor/quiz/parse-pdf`, `POST /professor/quiz/publish`,
`GET /professor/quizzes`, `PATCH|DELETE /professor/quizzes/{quiz_id}`,
`POST /professor/quiz/{quiz_id}/submit`, `POST /professor/module-quiz/review`,
`POST /professor/module-quiz/publish`, `GET /professor/module-quiz/{job_id}/{module_id}`,
`GET /professor/oak-questions`, `GET /professor/analytics`, `POST /professor/report`.
`GET /quizzes` is the unauthenticated student-facing view of published quizzes.

---

## 5. The existing reel engine (relevant to the visual-engine brief)

`reel_planner.py` (906 lines) already produces 1080x1920 @ 30fps MP4s:

- **Stage 1 (plan, LLM)** picks one concept out of a module, maps it to a
  `prep_mode` concept id, finds a real past-year question tagged to it, and
  writes a ~130-word script shaped `hook -> concept -> analogy -> PYQ tie-in + common mistake`.
- **Stage 2 (render)** narrates via edge-tts, times captions off the narration
  with whisper word timestamps, and composites **animated caption text on a
  solid brand background** with MoviePy. There is no avatar in v1.

Brand tokens are already lifted from `frontend/style.css`:
`bg #0a0a0a`, `text #f0ede6`, `accent #c8f060`, `muted #555550`.

`clipper` (the `reel` repo) is a second renderer: two static PNG character
cutouts over a classroom background, two-voice dialogue, ~50 seconds.

**Neither is a structured-instruction visual engine.** Both are
narration-plus-text compositors. There is no array visualization, no pointer,
no code editor, no terminal, no diagram primitive, and no timeline/component
JSON schema of the kind the brief describes. The `plan` dict is editorial
(concept, hook_angle, beats, script, pyq, common_mistake), not spatial.

So the Motion Canvas engine is **genuinely new work**, but it should consume a
plan produced the same way `plan_reel()` produces one, and be a third renderer
alongside the existing two rather than a replacement for either.

---

## 6. What already works end to end

1. Upload a lecture video or paste a YouTube URL; get it transcribed, segmented into modules, and cut into clips.
2. Per module: detailed markdown notes with an inline, linted Mermaid concept diagram.
3. Per module: LLM-generated MCQ quiz, submission, misconception diagnosis, mastery updates.
4. Prof Oak chat, RAG-grounded against the OS exam-paper corpus, with mastery context, struggle detection, web search, PDF/image attachment, and persistence.
5. Sandboxed code execution (glot.io) in multiple languages.
6. Prep Mode: concept ranking from past papers, predicted exam paper, backtesting, a navigable concept tree, per-concept teaching over `/chat`.
7. Research papers: PDF -> podcast, and a single-voice audio Q&A chat.
8. Assignments: publish, submit, review.
9. Professor tools: PDF -> quiz, quiz review/publish, analytics, "what are students asking Oak" reports.
10. Two working 9:16 reel renderers.

---

## 7. Technical debt and broken areas

Findings, not instructions. **Nothing here should be "fixed" as part of the frontend build** unless explicitly agreed.

1. **`clipper` is forked across two repos.** `reel/clipper` and `MAROS/clipper` have diverged in every source file. `reel` has `run_pyq.py` and topic mode; `MAROS` has `schema.sql`. There is no sync mechanism. This will bite whoever edits the wrong copy.
2. **`reel/clipper/generate_script.py` has 138 uncommitted lines** adding line-quality rules and a whole topic mode. Uncommitted work should be committed before anything else lands.
3. **The job store is in-memory.** `GET /jobs/{id}` 404s after a restart, mid-processing jobs are lost silently, and the design assumes exactly one server process. A processing-page frontend must fall back to `/lectures` and `/jobs/{id}/manifest`.
4. **The concept taxonomy is OS-only and has no "none" escape.** `reel`'s own README documents this: the keyword fallback hard-defaults to `processes`, so a non-OS lecture is mis-tagged and pulls irrelevant questions. **This directly blocks the Python / C/C++ / DS / Algorithms / AI-ML subjects in the brief.**
5. **`gpt-oss-120b` returns empty content on long prompts.** It is a reasoning model and chipper's Groq branch sends no `max_tokens` or `reasoning_effort`, so Groq's JSON validator 400s. `_llm_json()` retries with `reasoning_effort: "low"`, which works but is flaky.
6. **Script quality varies run to run.** Reels sometimes need re-rolling. Any UI over reel generation needs a visible regenerate affordance.
7. **`main.py` is 2262 lines and 95 KB** with routes, models, config, and pipeline glue interleaved. Adding endpoints there is increasingly risky.
8. **No tests anywhere.** No pytest, no CI, no fixtures, in either repo.
9. **`CORS_ORIGINS` defaults to `*`** while `allow_credentials=True`. Fine locally, wrong in production.
10. **Machine-specific absolute paths in committed code**: a Windows `DENO_PATH` fallback, a macOS-only default `REEL_FONT` (`/System/Library/Fonts/Supplemental/Arial Bold.ttf`), a hardcoded `~/Desktop/MAROS/config/cookies.txt`.
11. **`config.py` raises at import if `GROQ_API_KEY` is missing**, so the whole app fails to start rather than degrading.
12. **The Supabase schema is not in version control** except `reel_comments`.
13. **`/chat/upload` holds extracted text in a process-local dict**, so it is lost on restart and broken under more than one worker.
14. **Frontend files are 30-83 KB single HTML documents** with inline logic. Not reusable; effectively a rewrite target, but they are the only working UI today.
15. `MAROS/.env` is present on disk with live keys. Confirm it is gitignored before any push.

---

## 8. Things that must NOT be changed

- **`chipper.py`** — transcription, windowed segmentation, notes, Mermaid linting, clip cutting. The whole product sits on it.
- **The manifest contract** — `outputs/{job_id}/manifest.json` and the `Module`/`Manifest` shapes. `chipper`, `reel_planner`, `clipper`, `clipper_routes`, and the existing frontend all read it.
- **All on-disk path conventions**, including the `outputs/` vs `output/` split and the `Module_NN_<slug>.mp4` / `_notes.txt` filename patterns. Several modules glob these.
- **The auth contract** — `Authorization: Bearer` for students, `X-Prof-Token` for professors, and `get_current_user` returning `None` rather than raising.
- **Supabase table and RPC names** — `interaction_log`, `quiz_answers`, `oak_chats`, `reel_comments`; `student_mastery_summary`, `update_mastery`, `ready_to_learn`.
- **`prep_mode.CONCEPTS` ids.** Mastery rows, PYQ tags, and reel plans are all keyed on these strings. The taxonomy can be *extended*, but existing ids must not be renamed.
- **The existing frontend at `MAROS/frontend/`** and its `/app` + `/static` mounts, until the new frontend is at parity and the switch is deliberate.
- **The `reel` repo's read-only stance toward `MAROS`.** `clipper` reads `MAROS_ROOT`; it must never write there.
- **`reel_planner.py` and `clipper/`** as working renderers. The new visual engine is additive.
