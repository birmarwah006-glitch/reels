# MAROS — Product Specification

The canonical product definition, and an honest reading of what it changes
about the code that already exists.

Nothing was implemented from this document. It is a specification and a delta
analysis.

---

## 1. What MAROS is

> A free, Python-first, short-form learning platform where educational
> knowledge is transformed into audio-driven, visual, interactive learning
> experiences called **Meals**.

MAROS is **not** an AI video generator. The differentiator is not
"AI-generated videos" — it is turning complex educational content into small
understandable units that people can discover, consume, practise, and build
from.

```
REELS ARE THE FORMAT.
MEALS ARE MAROS'S FORMAT.
LEARNING IS THE PRODUCT.
```

---

## 2. Terminology — a hard rule

Instagram has Reels. YouTube has Shorts. TikTok has TikToks. **MAROS has
Meals.**

Product UI must never say "Reel".

**Important exception.** The backend already ships reel-named identifiers:
the `/reels/*` and `/clipper/reels/*` routes, the `reel_status` field,
`reel_planner.py`, and `reel_*.json` sidecars. The backend is the source of
truth and is not renamed. The rule governs **rendered strings**, not internal
names.

| Layer | Says |
|---|---|
| API paths, types, variables, filenames | `reel` — unchanged |
| Anything a learner reads | **Meal** |

### Violations in the shipped web app (to be fixed)

| File | Current string |
|---|---|
| `web/src/pages/Explore.tsx:52` | "Concept reels" |
| `web/src/pages/Lecture.tsx:112` | `<Badge>Reel</Badge>` |
| `web/src/components/ReelPlayer.tsx:67` | "This reel could not be loaded." |

Plus the component name `ReelPlayer` (internal, so optional — but renaming it
to `MealPlayer` keeps the codebase honest about the product's vocabulary).

---

## 3. What a Meal is

> One short, focused learning experience built around one concept, example,
> coding technique, project step, or insight.

- Typically **30–90 seconds**, but duration is **not a hard constraint**.
- **ONE MEAL = ONE LEARNING OBJECTIVE.** This is the governing rule.
- The objective is *"the shortest video that can teach the concept correctly"* —
  **not** "the shortest video possible".
- Never split content to inflate Meal count. Never sacrifice accuracy for
  brevity. If a concept needs two minutes, it takes two minutes.

### Meal structure

```
HOOK -> QUESTION / PROBLEM -> CONCEPT -> VISUAL EXPLANATION
     -> CODE / EXAMPLE -> EXECUTION / RESULT -> TAKEAWAY -> PRACTICE
```

Every Meal has audio. The format is **voice + visual + code + captions**, and
a learner should be able to follow it primarily by listening while watching.
The visual **supports the narration** rather than decorating it.

---

## 4. Architecture — the AI does not generate pixels

```
SOURCE -> LLM -> CONCEPT EXTRACTION -> SCRIPT -> HOOK -> VISUAL PLAN
       -> STRUCTURED JSON -> MAROS VISUAL RENDERER

SCRIPT -> TTS -> VOICE

VISUALS + CODE + AUDIO + CAPTIONS -> FFmpeg -> MEAL MP4
```

The LLM decides **what** is taught and **what appears**. The renderer decides
**how** it appears. No expensive generative-video API is on the critical path.
This keeps the system cheap, fast, deterministic, editable, and visually
consistent.

### Visual styles

The engine picks a style per concept — clarity first, not variety for its own
sake:

A. Visual (boxes, arrows, pointers) · B. Character · C. Whiteboard ·
D. Terminal · E. Memory visualisation · F. Code editor (VS-Code-like)

---

## 5. The wedge is Python

Not "every CS subject". Python first, because it has a huge beginner
audience, obvious AI/ML and automation relevance, highly visual concepts, and
executable code that can be demonstrated for real.

Categories: Fundamentals · Control Flow · Data Structures · Functions ·
Errors · OOP · Intermediate.

Launch catalogue ≈ **150 curated Meals** (~30 fundamentals, ~20 control flow,
~30 data structures, ~20 functions, ~20 OOP, ~10 errors, ~20 intermediate) to
avoid an empty feed. Curated by the team. Quality over quantity — a coherent
learning catalogue, not 150 unrelated videos.

Later, and only later: C++ -> DSA -> OS -> AI/ML -> broader.

---

## 6. The feed

Full-screen, vertical, swipeable, audio-first. The interaction pattern is
familiar from short-form apps; the branding, UI, and terminology are not
borrowed. The feed must not fill up with social-media chrome — priority is
video, voice, concept, code, learning action.

The point is to move the learner from passive consumption to active learning:

```
WATCH -> UNDERSTAND -> PRACTICE -> RELATED CONCEPT -> NEXT STEP
```

Meals are connected by a knowledge graph, so watching "how does a for loop
work" can lead to "what does range() actually do", then "when should you use
while instead of for".

---

## 7. V1 scope

**In:** Python Meals · vertical Meal feed · audio narration · visual/code
rendering · ~150 curated Meals · basic concept discovery · basic practice ·
content upload *if the backend already supports it*.

**Out:** massive notes system · PDF generation · flashcards · forums ·
complex social profiles · creator marketplace · 20 subjects · AI avatars ·
complex LMS · gamification · complicated analytics · subscriptions · ads.

Free for students. No paywall, no ads. The objective is validation: do people
watch, return, understand, practise, and build?

Future revenue (not now): contextual technical advertising (Raspberry Pi on an
IoT Meal, GPUs on an ML Meal), sponsored Meals, creator tools, institutional
products.

**Trust principle:** never modify an educational explanation because an
advertiser wants a conclusion. Educational truth and sponsored placement stay
separate.

**Do not overengineer:** no Docker, Kubernetes, Redis, microservices, or
queues unless the existing system actually requires them. Docker may make
sense later for rendering workers.

---

## 8. Delta against what exists today

This is where the specification and the current codebase disagree. Each item
is a real conflict, not a stylistic preference.

### 8.1 There are no Meals, and no Python content

The backend has no Meal concept, no Python material, and no curated
catalogue. What exists is one processed **discrete-mathematics** lecture
(`fcece514...`, "Predicates, Sets, and Proofs"), six modules, one rendered
29.9s vertical video. The 150-Meal Python catalogue is entirely unbuilt.

### 8.2 Sequencing conflict: V1 scope requires the deferred visual engine

Decision **D3** deferred the visual engine and put the web app first. But V1
scope (§7) is *Python Meals + a Meal feed + visual/code rendering + ~150
curated Meals*. Those Meals **cannot exist without the renderer**.

So D3 and the V1 scope now point in opposite directions. This needs an
explicit call:

- **(a)** Build the visual engine next, generate the Python catalogue, then
  reshape the frontend around a Meal feed. Matches this spec's V1.
- **(b)** Keep the web app first and let the Meal feed wait. Delays the core
  product.
- **(c)** Build the renderer for a handful of Meals to prove the pipeline,
  then decide. Smallest irreversible commitment.

### 8.3 "MAROS is not simply clipping the original video"

§16 of the spec is explicit. But `chipper.cut_clips()` **does** clip — it
ffmpeg-cuts `Module_NN_<slug>.mp4` out of the source, and those clips are
what `/modules/{job}/{m}/video` serves and what the lesson page plays today.

Clipping is a genuinely different product from transformation into new
learning units. The existing clip pipeline is not wrong — it is just not what
a Meal is. Both can coexist (the clip is useful "watch the source moment"
evidence), but the Meal must be newly rendered, not cut.

### 8.4 Meal count must follow learning objectives, not duration

`chipper` segments on a **20-minute sliding window** with `MAX_MODULES = 8`
and at most 4 modules per window. That is duration-driven. The spec is
explicit that source duration must not determine Meal count — learning
objectives do, so a 30-minute lecture might yield 8 Meals and a 3-hour project
30.

A Meal planner therefore cannot simply consume chipper's module boundaries. It
needs its own objective-extraction pass over the transcript.

### 8.5 The current feed is the wrong shape

The shipped Explore page is a **card grid**, discovery-by-browsing. The spec
wants a **full-screen vertical swipeable audio-first feed**. That is a
different page, not a restyle. The existing horizontal Meal rail on Explore is
a reasonable secondary surface but is not the primary experience.

### 8.6 V1 de-prioritises much of what the backend does best

The backend's most developed features — the detailed notes system, research
papers, podcast generation, Prep Mode, professor tools, PDF handling — are all
on the §7 "do not prioritise" list. The shipped lesson page is notes-centric.

**This is a prioritisation signal, not a deletion order.** Nothing gets
removed; the standing rule that working backend functionality is preserved
still holds. But new frontend effort should go to Meals, not to deepening
notes.

### 8.7 Practice needs real execution, and it half-exists

The loop is WATCH -> PREDICT -> TRY -> FEEDBACK -> RETRY -> UNDERSTAND ->
BUILD, and learners should execute code inside MAROS. `POST /chat/execute-code`
already does this for nine languages including Python.

Caveat: it is currently **unreachable from this machine** (`run.glot.io` does
not resolve), and it is a third-party dependency on the critical path of the
core learning loop. Worth a decision before it becomes load-bearing.

### 8.8 The knowledge graph does not exist

§6 needs a Python concept graph. The only taxonomy in the backend is
`prep_mode.CONCEPTS` — **12 hardcoded Operating Systems ids**, with a tagger
that (per `reel`'s own README) has no "none" escape and hard-defaults to
`processes`. It is unusable for Python and would mis-tag every Python Meal.

Already logged as GAP 2 in `frontend-api-gaps.md`; this spec raises it from
"blocking for five subjects" to **blocking for the wedge itself**.

---

## 9. What is genuinely reusable

Not everything conflicts. These carry over unchanged:

- **The two-stage plan/render split** in `reel_planner.py` — editorial
  decision first, rendering second — is exactly the architecture §4 describes.
- **Whisper word-level caption timing**, already producing per-word timings.
- **The TTS path** (`explainchat.render_chat_audio`, edge-tts) — audio is core
  and this already works, locally and free.
- **`POST /chat/execute-code`** for the EXECUTION beat and for practice.
- **9:16 / 1080x1920 / 30fps MoviePy + ffmpeg compositing**, already producing
  correct output.
- **The design tokens** shared across `frontend/style.css`, the web app, and
  `reel_planner.py`.
- **The whole ingest pipeline** — transcription and transcript handling — for
  the user-generated-content path in §16.
