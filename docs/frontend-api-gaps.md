# MAROS — Frontend API Gaps

Backend functionality the briefed frontend needs that **does not exist today**.

Written before any UI was built, so that no page is designed around an endpoint
that isn't there. Nothing in this file has been implemented. Nothing has been
mocked.

Ordered by how much frontend work each one blocks.

**Verified against the running backend.** Every claim below was checked with a
live request, not read off the source alone. Gaps 10-12 were found only by
running it.

---

## GAP 1 — Public concept catalogue (blocks the Explore page entirely)

**Feature**: Explore / Concept Feed. Browse concepts by subject without logging in.

**Required endpoint**: `GET /concepts`

**Expected request**: `?subject=python&difficulty=beginner&limit=40&cursor=`

**Expected response**:
```json
{
  "concepts": [
    {
      "concept_id": "python-for-loops",
      "title": "For loops",
      "subject": "python",
      "description": "Repeat an operation for each item in a sequence.",
      "difficulty": "beginner",
      "estimated_minutes": 4,
      "preview_url": "/concepts/python-for-loops/preview.mp4",
      "prerequisites": ["python-lists"],
      "has_code": true
    }
  ],
  "next_cursor": null
}
```

**Why it is needed**: The brief's Explore page is a public catalogue keyed on
atomic concepts. What exists is `GET /lectures` — a list of *processed lecture
jobs*, whose `concept` strings are lecture segments, not teachable units. A real
one reads `"Course logistics, assessment policies, and collaboration guidelines"`.
Those cannot be browsed as concept cards.

`prep_mode.CONCEPTS` is the only concept registry, and it is 12 hardcoded
Operating Systems ids with no title, difficulty, duration, or subject field.

**Blocks**: Explore page, landing-page example cards, concept search.

---

## GAP 2 — Subject taxonomy beyond Operating Systems

**Feature**: The brief names Python, C/C++, Data Structures, Algorithms,
Operating Systems, and AI/ML.

**Required**: a `subject` dimension above the concept taxonomy, plus concept
sets for the five non-OS subjects.

**Why it is needed**: Every concept-aware code path is keyed on the 12 OS ids in
`prep_mode.CONCEPTS` — the tagger, PYQ matching, mastery scoring, reel planning,
prep mode. `reel`'s own README records that the tagger has **no "none" escape**
and its keyword fallback **hard-defaults to `processes`**, so a Python lecture is
silently mis-tagged and pulls irrelevant OS exam questions.

**Blocks**: every subject in the brief except Operating Systems. This is the
single largest gap between the product described and the backend that exists.

**Note**: `CONCEPTS` ids must be *extended*, never renamed — existing Supabase
mastery rows and PYQ tags reference them as strings.

---

## GAP 3 — Concept lesson content

**Feature**: The Concept Learning Page — hook, explanation, visual, code
example, execution, takeaway, quiz.

**Required endpoint**: `GET /concepts/{concept_id}/lesson`

**Expected response**:
```json
{
  "concept_id": "python-for-loops",
  "title": "For loops",
  "hook": "You have written the same line five times. Stop.",
  "explanation": "A for loop lets a program repeat an operation for each item in a sequence.",
  "visual": { "type": "array_iteration", "values": [1,2,3,4,5] },
  "code": { "language": "python", "source": "for number in numbers:\n    print(number)" },
  "expected_output": "1\n2\n3\n4\n5",
  "takeaway": "One loop, one line, every element.",
  "quiz_id": "q_python_for_loops"
}
```

**Why it is needed**: Nothing serves lesson-structured content. The closest
thing is `GET /modules/{job_id}/{module_id}/notes`, which returns free-form
markdown with an inline Mermaid block at the `%%CONCEPT_MAP%%` token. That is
lecture notes, not a seven-beat lesson, and it only exists for uploaded
lectures — never for a catalogue concept.

**Blocks**: the Concept Learning Page, which the brief calls the most important
page in the product.

**Partial reuse available**: `POST /chat/execute-code` already runs the code and
returns `{stdout, stderr, exit_code}`. Only the lesson body is missing.

---

## GAP 4 — Contextual tutor actions

**Feature**: The `[Explain simpler]` `[Give me an analogy]` `[Show me visually]`
`[Show me code]` `[Quiz me]` buttons.

**Required**: either a typed endpoint —
`POST /concepts/{concept_id}/action` with `{action: "simpler"|"analogy"|"visual"|"code"|"quiz"}` —
or a documented commitment that the frontend composes these as `/chat` prompts.

**Why it is needed**: `POST /chat` exists and can answer all five, but it takes
`{job_id, module_id, paper_id}` for context. It has **no `concept_id` parameter**,
so a catalogue concept with no backing lecture cannot be given context. `mode`
accepts `videos | papers | assignments | prep` — no concept mode.

**Recommendation**: extend `ChatRequest` with an optional `concept_id` and a
`mode: "concept"`. That is additive and does not disturb the four existing modes.

**Blocks**: the tutor action bar on the Concept Learning Page.

---

## GAP 5 — Per-lecture concept graph

**Feature**: The Generated Course / Concept Map — a visual learning path with
completed / current / locked states and prerequisite edges.

**Required endpoint**: `GET /lectures/{job_id}/graph`

**Expected response**:
```json
{
  "nodes": [
    {"concept_id": "processes", "title": "Processes", "module_id": 1,
     "status": "done", "estimated_minutes": 6}
  ],
  "edges": [{"from": "processes", "to": "threads"}]
}
```

**Why it is needed**: `GET /modules/{job_id}` returns a flat, time-ordered
`Module[]` with no prerequisite relationships. `GET /prep/tree` does return
concepts with statuses, but it is the **fixed 12-concept OS syllabus**, not the
concepts extracted from the user's own lecture, and it hard-requires login.

There is no ordering signal in a manifest beyond timestamp order.

**Blocks**: the Concept Map page. A time-ordered vertical path can ship in the
interim using `/modules/{job_id}` — honest, since lecture order is real
information — but prerequisite edges and lock states cannot.

---

## GAP 6 — Course-level progress

**Feature**: My Learning — `Operating Systems ████████░░ 80%`.

**Required endpoint**: `GET /student/progress`

**Expected response**:
```json
{
  "courses": [
    {"course_id": "os", "title": "Operating Systems",
     "concepts_total": 12, "concepts_done": 9, "percent": 75,
     "last_seen_at": "2026-08-20T18:00:00Z",
     "resume": {"job_id": "...", "module_id": 4}}
  ]
}
```

**Why it is needed**: `GET /student/mastery` returns per-concept scores from the
`student_mastery_summary` RPC, with no course grouping and no percentage roll-up.
`GET /student/classwork` returns quiz attempts grouped by module. Neither answers
"how far through Operating Systems am I" or "where do I resume".

`course_id` is accepted as a query parameter across several endpoints but is
never populated anywhere in the codebase.

**Blocks**: the progress bars on My Learning, and the "Continue learning" card.

**Partial reuse**: mastery scores can drive a rough per-concept bar today.

---

## GAP 7 — Durable job status

**CORRECTION, found while building the processing page.** The workaround
originally noted here did not work, because `/jobs/{id}/manifest` is **not**
disk-backed either — `main.py get_manifest` looks the job up in the same
in-memory store before it ever touches the filesystem:

```python
job = jobs.get_job(job_id)
if not job:
    raise HTTPException(status_code=404, ...)
```

So after a restart **both** `/jobs/{id}` and `/jobs/{id}/manifest` return 404
even though `outputs/{id}/manifest.json` exists. Verified live against the one
processed lecture:

```
/jobs/{id}           404
/jobs/{id}/manifest  404
/modules/{id}        200
```

The genuinely restart-safe endpoints are `/modules/{id}` and `/lectures`,
which read the manifest straight off disk. The shipped fallback chain is
therefore three deep, and it works. The gap below still stands.



**Feature**: The Processing / Analysis page — "Analyzing lecture / Extracting
concepts / Building learning path".

**Required**: persist job records, or add `GET /jobs/{job_id}` fallback behaviour
that reconstructs status from disk.

**Why it is needed**: `jobs.py` is an in-memory `dict`. `GET /jobs/{job_id}`
**404s after a server restart**, and a job that was mid-processing is lost with
no failure record. The frontend cannot distinguish "this job never existed" from
"the server restarted" from "processing failed".

**Blocks**: reliable processing UI. **Workaround shipped**: poll
`GET /jobs/{id}`; on 404 try `GET /jobs/{id}/manifest` (202 while processing);
on 404 again fall through to the disk-backed `GET /modules/{id}`, which proves
the work finished and only the record was lost. Implemented in
`web/src/api/hooks.ts` as `useJobProgress`.

A one-line fix in `main.py` would remove the need for any of it: read the
manifest off disk when the in-memory job is missing, instead of 404ing.

---

## GAP 8 — Clipper reel generation over HTTP

**Feature**: Trigger a two-character explainer reel from the UI.

**Required endpoint**: `POST /clipper/reels/{job_id}/{module_id}`

**Why it is needed**: `clipper_routes.py` is **read-only** — it lists reels,
serves video, and handles comments. Generation is CLI-only
(`python -m clipper.run_clipper <job_id>`), and it lives in a *different repo*
whose copy of the package has diverged from `MAROS/clipper/`.

`reel_routes.py` does expose `POST /reels/{job_id}/{module_id}` for the
`reel_planner` renderer, so the pattern to copy already exists.

**Blocks**: nothing in the current build. Reels are consumable read-only today,
which is enough for the Explore feed.

---

## GAP 9 — Visual lesson instruction schema

**Feature**: The MAROS Visual Engine — AI emits structured instructions, the
engine renders them.

**Required endpoint**: `GET /concepts/{concept_id}/visual` returning the
`{concept, visualStyle, components[], timeline[]}` shape from the brief.

**Why it is needed**: `reel_planner.plan_reel()` returns an **editorial** plan —
`{concept, concept_id, hook_angle, beats, script, pyq, common_mistake,
word_count}`. It contains no components, no timeline, and no spatial
information. There is no schema anywhere in either repo that describes what to
draw.

**Blocks**: nothing yet. The first Motion Canvas example (Python for loop) will
be built against a **hand-authored** lesson JSON file checked into the visual
engine, with the schema defined there first. Wiring an LLM to emit that schema
is a later, separate step — and the schema should be proven by a working
renderer before any prompt is written against it.

---

## GAP 10 — Reel thumbnails / poster frames

**Feature**: Explore feed cards, and a poster frame before a reel plays.

**Required**: a populated `thumbnail_url`, or `GET /reels/{job_id}/{module_id}/poster`
returning a JPEG.

**Expected response**: `image/jpeg`, one frame from the reel.

**Why it is needed**: `GET /clipper/reels/{job_id}` returns `thumbnail_url` but
it is **hardcoded `None`** (`clipper_routes.py`). `reel_routes.py` has no
thumbnail concept at all. Verified live: the field comes back `null`.

Without a poster, a feed of reels is a grid of black rectangles until each one
buffers its first frame.

**Blocks**: nothing hard. **Interim approach**: the video element gets
`preload="metadata"` and the card renders the concept title over the brand
surface until playback starts. No fake image is generated.

---

## GAP 11 — Lecture-level delete / re-run

**Feature**: Managing your own uploaded lectures in My Learning.

**Required**: `DELETE /lectures/{job_id}` and `POST /lectures/{job_id}/reprocess`,
or a module-level `POST /modules/{job_id}/{module_id}/regenerate-notes`.

**Why it is needed**: Verified live — **2 of the 6 modules in the only processed
lecture carry `chipper._NOTES_FAILED_SENTINEL`**:

```
[Notes generation failed — this module needs to be regenerated]
```

The sentinel says the module "needs to be regenerated", but **no endpoint can
regenerate it**. `/papers/{id}` has a DELETE; lectures have none. The user's only
recourse is re-uploading the entire video.

Given the documented `gpt-oss-120b` empty-completion flakiness, partial failure
is a normal outcome, not a rare one.

**Blocks**: recovery from partial processing failure. The lesson page will
detect the sentinel and say so honestly, but cannot offer a fix.

---

## GAP 12 — Aggregate reel listing for the feed

**Feature**: An Explore feed that opens with reels across all lectures.

**Required**: `GET /reels?limit=&cursor=`

**Why it is needed**: Reels can only be listed **per job** — `GET /reels/{job_id}`.
Building a cross-lecture feed means calling `GET /lectures` and then fanning out
one request per job. That is acceptable at today's scale (verified live: **1
lecture with a manifest**, out of 13 directories in `outputs/`) and untenable at
a hundred.

**Blocks**: nothing now. Implemented as a client-side fan-out with a documented
ceiling.

---

## Summary

| # | Gap | Blocks | Severity |
|---|---|---|---|
| 1 | Public concept catalogue | Explore page | Blocking |
| 2 | Non-OS subject taxonomy | 5 of 6 subjects | Blocking |
| 3 | Concept lesson content | Concept Learning page | Blocking |
| 4 | Tutor actions with concept context | Action bar | High |
| 5 | Per-lecture concept graph | Concept Map | Medium, workaround exists |
| 6 | Course-level progress | My Learning | Medium, partial data exists |
| 7 | Durable job status | Processing page | Medium, workaround exists |
| 8 | Clipper generation over HTTP | Reel authoring | Low |
| 9 | Visual instruction schema | Visual engine | Deferred per D3 |
| 10 | Reel thumbnails | Feed polish | Low, interim exists |
| 11 | Lecture delete / regenerate | Failure recovery | **High — real data is already affected** |
| 12 | Aggregate reel listing | Feed scale | Low, fan-out for now |

Gap 11 deserves attention sooner than its position suggests: it is the only gap
that affects **data that already exists on disk today**.

Gaps 1, 2, and 3 are one problem wearing three hats: **MAROS has lecture-derived
content, and the brief describes a concept-first catalogue.** No amount of
frontend work bridges that. It needs a backend decision, and it is the first
thing worth deciding.
